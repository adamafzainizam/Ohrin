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

export function pickAudioFile(files) {
  for (const f of files || []) {
    if (String(f.type || '').startsWith('audio/') || AUDIO_EXT.test(f.name || '')) return f;
  }
  return null;
}
