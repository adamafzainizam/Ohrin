// Pure view model for the first-run setup panel. The controller in setup-ui.js owns the DOM and the network.
const NO_DATA = 'No audio or usage data is sent.';

export function formatBytes(n) {
  if (!Number.isFinite(n) || n < 0) return '';
  if (n >= 1e6) { const mb = n / 1e6; return `${mb >= 100 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`; }
  return `${Math.max(1, Math.round(n / 1e3))} KB`;
}

function progressView(progress) {
  const bytes = Number(progress?.bytes), total = Number(progress?.total);
  if (!(total > 0) || !Number.isFinite(bytes)) return { percent: null, valueText: 'Downloading, size unknown' };
  const percent = Math.min(100, Math.max(0, Math.round((bytes / total) * 100)));
  return { percent, valueText: `${percent} percent, ${formatBytes(Math.max(0, Math.min(bytes, total)))} of ${formatBytes(total)}` };
}

export function setupView(setup, progress, { dismissed = false } = {}) {
  const hidden = { visible: false, canDownload: false, showConsent: false, sizeText: '', hosts: [], destination: '', message: '', percent: null, valueText: '', showCancel: false, showRetry: false };
  if (!setup || typeof setup !== 'object') return hidden;
  const state = progress?.state || 'idle';
  const list = Array.isArray(setup.assets) ? setup.assets : [];
  const missing = list.some(a => !a.verified);
  const base = {
    ...hidden,
    sizeText: formatBytes(Number(setup.download_bytes)),
    hosts: Array.isArray(setup.hosts) ? setup.hosts : [],
    destination: setup.data_dir || '',
  };
  if (state === 'downloading') {
    return { ...base, visible: true, showCancel: true, ...progressView(progress),
      message: progress.file ? `Downloading ${progress.file}` : 'Starting the download' };
  }
  if (state === 'done') {
    return { ...base, visible: !dismissed, message: 'Verified ✓. Instrument candidates will appear on your next analysis.' };
  }
  if (state === 'error' || state === 'cancelled') {
    const retry = Boolean(setup.torch) && Boolean(setup.downloadable);
    return { ...base, visible: !dismissed, showRetry: retry,
      message: state === 'cancelled' ? 'Download cancelled. Nothing was kept.' : (progress.error || 'The download failed.') };
  }
  if (!missing || dismissed) return hidden;
  if (!setup.torch) return { ...base, visible: true, message: 'Instrument candidates need PyTorch, which this installation does not have.' };
  if (!setup.downloadable) return { ...base, visible: true, message: 'The instrument model is not available yet, so instrument candidates are off. Everything else works.' };
  return { ...base, visible: true, canDownload: true, showConsent: true,
    message: `Instrument candidates need a one-time download of about ${base.sizeText}. ${NO_DATA}` };
}
