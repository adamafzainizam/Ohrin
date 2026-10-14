export function clampSelection(a, b, duration) {
  const limit = Math.max(0, Number(duration) || 0);
  const x = Math.max(0, Math.min(limit, Number(a) || 0));
  const y = Math.max(0, Math.min(limit, Number(b) || 0));
  return { start: Math.min(x, y), end: Math.max(x, y) };
}

export function shouldLoop(time, selection, enabled) {
  return Boolean(enabled && selection.end > selection.start && time >= selection.end);
}

export function shouldStop(time, selection, loopEnabled) {
  return Boolean(!loopEnabled && selection.end > selection.start && time >= selection.end);
}

export function formatTime(seconds) {
  const n = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
}

export function measuredObservations(windows) {
  if (!Array.isArray(windows) || windows.length < 2) return [];
  let largest = null;
  for (let i = 1; i < windows.length; i++) {
    const drop = windows[i - 1].rms_dbfs - windows[i].rms_dbfs;
    if (!largest || drop > largest.drop) largest = { at: windows[i].start_s, drop };
  }
  const remarks = [];
  if (largest.drop >= 3) remarks.push(`At ${formatTime(largest.at)}, one-second RMS level fell ${largest.drop.toFixed(1)} dB from the previous window.`);
  const first = windows[0].band_fraction || [];
  const last = windows.at(-1).band_fraction || [];
  if (first.length === 3 && last.length === 3) {
    const highChange = Math.round((last[2] - first[2]) * 100);
    if (Math.abs(highChange) >= 5) remarks.push(`High-frequency power share changed ${highChange > 0 ? '+' : ''}${highChange} percentage points from the first to last analysed second.`);
  }
  return remarks;
}
