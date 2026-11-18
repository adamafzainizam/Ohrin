// Pure helpers for the installed app: where Export writes, and what /api/setup changes in the page.
export function exportTarget(win) {
  return typeof win?.pywebview?.api?.save_note === 'function' ? 'native' : 'download';
}

// pywebview injects its api after the page loads. Inside its window, wait for it before choosing a target.
export async function resolveExportTarget(win, timeoutMs = 3000) {
  if (exportTarget(win) === 'native' || !win?.pywebview) return exportTarget(win);
  await new Promise((resolve) => {
    let timer = null;
    const done = () => { clearTimeout(timer); win.removeEventListener?.('pywebviewready', done); resolve(); };
    timer = setTimeout(done, timeoutMs);
    win.addEventListener?.('pywebviewready', done);
  });
  return exportTarget(win);
}

export function desktopView(setup) {
  const ffmpeg = setup && typeof setup === 'object' ? setup.ffmpeg : null;
  // A missing or failed setup response gates nothing: only an explicit "not found" blocks analysis.
  const ffmpegMissing = Boolean(ffmpeg) && typeof ffmpeg === 'object' && ffmpeg.found === false;
  const hint = ffmpegMissing && ffmpeg.hint ? ffmpeg.hint : 'your package manager (see the FFmpeg download page)';
  return {
    heartbeat: Boolean(setup) && typeof setup === 'object' && setup.mode === 'browser',
    showQuit: Boolean(setup) && typeof setup === 'object' && setup.mode === 'browser',
    ffmpegMissing,
    ffmpegMessage: ffmpegMissing ? `FFmpeg is needed to analyse audio. Install it with: ${hint}` : '',
  };
}

// Browser mode only: the page tells the launcher it is still open, so closing the tab stops the app.
export const HEARTBEAT_MS = 10000;
export function startHeartbeat(win, setup, { fetchFn = win?.fetch?.bind(win), setIntervalFn = win?.setInterval?.bind(win) } = {}) {
  if (!desktopView(setup).heartbeat || !fetchFn || !setIntervalFn) return null;
  const beat = () => { try { fetchFn('/api/heartbeat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => {}); } catch (error) { /* the next beat retries */ } };
  beat();
  return setIntervalFn(beat, HEARTBEAT_MS);
}
