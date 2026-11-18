// First-run setup panel controller. Its state lives in this module; nothing downloads without the Download click.
import { setupView } from './setup.mjs';

const POLL_MS = 500;
const $ = id => document.getElementById(id);

export function initSetupPanel({ announce, loadSetup, focusAfterClose }) {
  let setup = null;
  let progress = { state: 'idle', file: null, bytes: 0, total: 0, error: null };
  let dismissed = false;
  let timer = null;
  let lastMilestone = -1;
  let lastState = 'idle';

  const post = path => fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: path.endsWith('download') ? '{"consent":true}' : '{}' });

  function render() {
    const view = setupView(setup, progress, { dismissed });
    const panel = $('setupPanel');
    const hadFocus = panel.contains(document.activeElement);
    panel.hidden = !view.visible;
    if (!view.visible) return view;
    $('setupMessage').textContent = progress.state === 'downloading' ? `${view.message} (${view.valueText})` : view.message;
    const facts = $('setupFacts');
    facts.hidden = !view.showConsent;
    $('setupSize').textContent = view.sizeText;
    $('setupHosts').textContent = view.hosts.join(', ');
    $('setupDest').textContent = view.destination;
    const bar = $('setupProgress');
    const downloading = progress.state === 'downloading';
    bar.hidden = !downloading;
    if (downloading) {
      if (view.percent === null) { bar.removeAttribute('value'); } else { bar.value = view.percent; }
      bar.setAttribute('aria-valuetext', view.valueText);
    }
    $('setupDownload').hidden = !view.canDownload;
    $('setupDownload').textContent = `Download (≈${view.sizeText})`;
    $('setupDismiss').hidden = downloading;
    $('setupDismiss').textContent = view.showConsent || view.canDownload || view.showRetry ? 'Not now' : 'Close';
    $('setupCancel').hidden = !view.showCancel;
    $('setupRetry').hidden = !view.showRetry;
    // A button that was just hidden took focus with it; hand focus to the next action, or the panel title.
    if (hadFocus && !panel.contains(document.activeElement)) {
      const next = [$('setupCancel'), $('setupRetry'), $('setupDownload'), $('setupDismiss')].find(b => !b.hidden);
      (next || $('setupTitle')).focus();
    }
    return view;
  }

  function note(view) {
    if (progress.state !== lastState) {
      lastState = progress.state;
      lastMilestone = -1;
      if (progress.state === 'downloading') announce('Download started');
      else if (['done', 'error', 'cancelled'].includes(progress.state)) announce(view.message);
    }
    if (progress.state === 'downloading' && view.percent !== null) {
      const step = Math.floor(view.percent / 25);
      if (step > lastMilestone && step > 0) { lastMilestone = step; announce(`${progress.file || 'Download'} ${view.percent} percent`); }
    }
  }

  async function poll() {
    try {
      const response = await fetch('/api/setup/progress');
      if (response.ok) progress = await response.json();
    } catch (error) { /* keep the last snapshot; try again */ }
    const view = render();
    note(view);
    if (progress.state === 'downloading') {
      timer = setTimeout(poll, POLL_MS);
    } else {
      timer = null;
      if (progress.state === 'done') { setup = (await loadSetup()) || setup; note(render()); }
    }
  }
  function startPolling() { if (timer === null) timer = setTimeout(poll, POLL_MS); }

  async function begin() {
    try {
      const response = await post('/api/setup/download');
      const body = await response.json();
      if (response.status === 202 || response.status === 409) {
        progress = response.status === 202 ? body : { ...progress, state: 'downloading' };
      } else {
        progress = { state: 'error', file: null, bytes: 0, total: 0, error: body.error || 'The download could not start.' };
      }
    } catch (error) {
      progress = { state: 'error', file: null, bytes: 0, total: 0, error: 'The download could not start.' };
    }
    dismissed = false;
    note(render());
    if (progress.state === 'downloading') startPolling();
  }

  $('setupDownload').addEventListener('click', begin);
  $('setupRetry').addEventListener('click', begin);
  $('setupCancel').addEventListener('click', async () => {
    $('setupCancel').disabled = true;
    try { await post('/api/setup/cancel'); } catch (error) { /* the next poll shows the truth */ }
    $('setupCancel').disabled = false;
    announce('Cancelling the download');
    startPolling();
  });
  $('setupDismiss').addEventListener('click', () => {
    dismissed = true;
    render();
    (focusAfterClose?.() || $('openButton'))?.focus?.();
  });

  return {
    // Called with each /api/setup response. Resumes the display if a download is already running on the server.
    async show(next) {
      setup = next;
      try {
        const response = await fetch('/api/setup/progress');
        if (response.ok) progress = await response.json();
      } catch (error) { /* idle is a safe default */ }
      lastState = progress.state;
      render();
      if (progress.state === 'downloading') startPolling();
    },
  };
}
