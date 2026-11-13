// Pure input helpers for the workbench.
const TYPING_TAGS = new Set(['INPUT', 'SELECT', 'TEXTAREA']);
const AUDIO_EXT = /\.(mp3|wav|flac|ogg|m4a)$/i;

export function zoomKeyAllowed(target) {
  if (!target) return true;
  if (target.isContentEditable) return false;
  if (target.tagName && TYPING_TAGS.has(String(target.tagName).toUpperCase())) return false;
  if (typeof target.closest === 'function' && target.closest('#eqPanel')) return false;
  return true;
}

export function zoomFactorFromWheel({ deltaY, deltaMode }) {
  const px = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 800 : deltaY;
  return Math.exp(-px * 0.002);
}

export function wheelIntent({ ctrlKey, metaKey, shiftKey, deltaX = 0, deltaY = 0 }) {
  if (ctrlKey || metaKey) return 'zoom';
  if (shiftKey || Math.abs(deltaX) > Math.abs(deltaY)) return 'pan';
  return null;
}

export function pickAudioFile(files) {
  for (const f of files || []) {
    if (String(f.type || '').startsWith('audio/') || AUDIO_EXT.test(f.name || '')) return f;
  }
  return null;
}

// Zoom anchors on the playhead only while it is inside the view; otherwise on the view centre.
export function zoomAnchor(view, playheadTime, playing) {
  const centre = (view.start_s + view.end_s) / 2;
  if (!playing || !Number.isFinite(playheadTime)) return centre;
  return playheadTime >= view.start_s && playheadTime <= view.end_s ? playheadTime : centre;
}

// Runs `fn` at most once per animation frame. The caller reads the latest state inside `fn`.
export function createCoalescer(fn, { request = cb => requestAnimationFrame(cb), cancel = id => cancelAnimationFrame(id) } = {}) {
  let id = null;
  return {
    schedule() { if (id === null) id = request(() => { id = null; fn(); }); },
    cancel() { if (id !== null) { cancel(id); id = null; } },
    get pending() { return id !== null; },
  };
}

// Live-region writer. Messages inside one window are joined so none is lost.
// Each call clears the region first so a repeated identical message is announced again.
export function createAnnouncer({ write, schedule = (fn, ms) => setTimeout(fn, ms), delay = 50 }) {
  let timer = null, pending = [];
  return function announce(text) {
    pending.push(String(text));
    write('');
    if (timer !== null) return;
    timer = schedule(() => {
      timer = null;
      const message = pending.join('. ').replace(/\.\. /g, '. ');
      pending = [];
      write(message);
    }, delay);
  };
}
