import { createOverviewLane } from './lanes/overview.js';
import { createRulerLane } from './lanes/ruler.js';
import { createWaveformLane } from './lanes/waveform.js';
import { createEnergyLane } from './lanes/energy.js';
import { createInstrumentsLane } from './lanes/instruments.js';
import { createPlayhead } from './lanes/playhead.js';
import { initEq } from './eq-ui.js';
import { initTheme, getPalette, onInvalidate, observeCanvasHost } from './theme.mjs';
import { createStore, fileLoaded, isStale } from './store.mjs';
import { createTransport } from './transport.js';
import { createInspector } from './inspector.js';
import { fit, formatTenths, zoomAt, pan, followPage, xToTime } from './timeline.mjs';
import { pickAudioFile, zoomKeyAllowed, zoomFactorFromWheel } from './input-rules.mjs';
import { clampSelection, formatTime, reviewKey, reconcileReview, pruneReview,
  settleRequest, buildPassageNote } from './state.mjs';

const $ = id => document.getElementById(id);
const fileInput = $('audioFile');
const player = $('player');
const canvas = $('waveform');
const store = createStore();
let objectUrl = null;
let requestSeq = 0;
const eq = initEq({ player, announce });
const transport = createTransport({ player, store, announce, resume: () => eq.resume(), isLooping: () => $('loop').checked });
transport.bindControls({ playButton: $('playButton'), speed: $('speed') });
const inspector = createInspector({ store, announce, transport });
const overviewLane = createOverviewLane();
const rulerLane = createRulerLane();
const waveformLane = createWaveformLane();
const energyLane = createEnergyLane();
const instrumentsLane = createInstrumentsLane();
const playhead = createPlayhead();
const actions = {
  select: updateSelection,
  seek: t => transport.seek(t),
  setView: view => setUserView(view),
  selectBlock(index) {
    store.set('ui', { activeTab: 'instruments', selectedBlock: index });
    document.querySelector(`[data-focus-id="seg-${index}"]`)?.focus({ preventScroll: true });
    const box = $('inspector').getBoundingClientRect();
    if (window.matchMedia('(max-width: 1279px)').matches && (box.top >= window.innerHeight || box.bottom <= 0)) {
      $('inspector').scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
    }
    announce(`Block ${index + 1} selected. Marking controls are in the Instruments tab.`);
  },
};
overviewLane.mount($('overview'), actions);
rulerLane.mount($('ruler'), actions);
waveformLane.mount(canvas, actions);
energyLane.mount($('energyChart'), actions);
instrumentsLane.mount($('instrumentTimeline'), actions);
playhead.mount($('playhead'), actions);
function laneArgs() {
  return { state: { ...store.get(), findingsStale: isStale(store.get()) }, view: store.get().view, widthPx: Math.max(1, canvas.getBoundingClientRect().width), palette: getPalette() };
}
function preserveFocus(fn) {
  const focusId = document.activeElement?.dataset?.focusId;
  fn();
  if (focusId) document.querySelector(`[data-focus-id="${CSS.escape(focusId)}"]`)?.focus({ preventScroll: true });
}
function renderLanes(full = false) {
  if (!store.get().duration || store.get().view.end_s <= store.get().view.start_s) return;
  const args = laneArgs();
  for (const lane of [overviewLane, rulerLane, waveformLane, energyLane, playhead]) lane.render(args);
  if (full) instrumentsLane.render(args); else instrumentsLane.reposition(args);
}
store.subscribe('view', () => renderLanes());
store.subscribe('selection', () => {
  if (!store.get().duration || store.get().view.end_s <= store.get().view.start_s) return;
  const args = laneArgs(); overviewLane.render(args); waveformLane.render(args); energyLane.render(args);
  renderStale();
});
let lastLaneUi = store.get();
store.subscribe('analysis', () => preserveFocus(() => {
  renderLanes(true); inspector.render('analysis'); renderStale();
  // Analysis already rendered its final loading state. Do not rebuild the
  // same lane DOM again when the request's finally block publishes ui.
  lastLaneUi = store.get();
}));
store.subscribe('review', () => preserveFocus(() => {
  instrumentsLane.update(store.get()); inspector.render('review');
}));
store.subscribe('ui', state => {
  if (state.file !== lastLaneUi.file || state.duration !== lastLaneUi.duration || state.analysing !== lastLaneUi.analysing) {
    if (state.duration) renderLanes(true);
  }
  if (state.activeTab !== lastLaneUi.activeTab || state.selectedBlock !== lastLaneUi.selectedBlock) {
    preserveFocus(() => { instrumentsLane.update(state); inspector.render('ui'); });
  }
  lastLaneUi = state;
  for (const id of ['zoomOut', 'zoomIn', 'fitPassage', 'fitTrack']) $(id).disabled = !state.duration;
});
const ICON_PLAY = '<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232.4,114.49,88.32,26.35a16,16,0,0,0-16.2-.3A15.86,15.86,0,0,0,64,39.87V216.13A15.94,15.94,0,0,0,80,232a16.07,16.07,0,0,0,8.36-2.35L232.4,141.51a15.81,15.81,0,0,0,0-27ZM80,215.94V40l143.83,88Z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M200,32H160a16,16,0,0,0-16,16V208a16,16,0,0,0,16,16h40a16,16,0,0,0,16-16V48A16,16,0,0,0,200,32Zm0,176H160V48h40ZM96,32H56A16,16,0,0,0,40,48V208a16,16,0,0,0,16,16H96a16,16,0,0,0,16-16V48A16,16,0,0,0,96,32Zm0,176H56V48H96Z"/></svg>';
// Registered after transport.bindControls, so these icons replace its text glyphs.
player.addEventListener('play', () => { $('playButton').innerHTML = ICON_PAUSE; });
player.addEventListener('pause', () => { $('playButton').innerHTML = ICON_PLAY; });
function updateReadout(t) {
  const d = Number.isFinite(player.duration) ? player.duration : 0;
  setText('timeReadout', `${formatTenths(t)} / ${formatTenths(d)}`);
}
transport.onFrame(t => {
  updateReadout(t);
  const { view, duration, followPaused } = store.get();
  if (!player.paused && duration && !followPaused) setView(followPage(view, t, duration));
  playhead.move(t);
});
player.addEventListener('play', () => store.set('ui', { followPaused: false }));

let viewAnnouncement = null;
function setView(view) {
  const current = store.get().view;
  if (view.start_s !== current.start_s || view.end_s !== current.end_s) store.set('view', { view });
}
function setUserView(view) {
  setView(view);
  if (!player.paused) store.set('ui', { followPaused: true });
  clearTimeout(viewAnnouncement);
  viewAnnouncement = setTimeout(() => {
    const current = store.get().view;
    announce(`Showing ${formatTenths(current.start_s)} to ${formatTenths(current.end_s)}`);
  }, 500);
}
function zoom(factor) {
  const { view, duration } = store.get();
  if (!duration) return;
  const anchor = player.paused ? (view.start_s + view.end_s) / 2 : player.currentTime;
  setUserView(zoomAt(view, anchor, factor, duration));
}
$('zoomOut').addEventListener('click', () => zoom(1 / 1.5));
$('zoomIn').addEventListener('click', () => zoom(1.5));
$('fitPassage').addEventListener('click', () => {
  const { selection, duration } = store.get();
  if (duration) setUserView(fit(selection.start, selection.end, duration));
});
$('fitTrack').addEventListener('click', () => {
  const { duration } = store.get();
  if (duration) setUserView(fit(0, duration, duration));
});
const laneStack = $('laneStack');
laneStack.addEventListener('wheel', event => {
  const { view, duration } = store.get();
  if (!duration) return;
  const rect = canvas.getBoundingClientRect();
  if (event.ctrlKey || event.metaKey) {
    event.preventDefault();
    const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
    const anchor = xToTime(x, view, Math.max(1, rect.width));
    setUserView(zoomAt(view, anchor, Math.max(.01, Math.min(100, zoomFactorFromWheel(event))), duration));
  } else if (event.shiftKey || event.deltaX) {
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? rect.width : 1;
    const delta = event.deltaX || event.deltaY;
    setUserView(pan(view, delta * unit / Math.max(1, rect.width) * (view.end_s - view.start_s), duration));
  }
}, { passive: false });
laneStack.addEventListener('keydown', event => {
  if (!laneStack.contains(event.target) || !zoomKeyAllowed(event.target)) return;
  if (!['+', '=', '-'].includes(event.key)) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  event.preventDefault(); zoom(event.key === '-' ? 1 / 1.5 : 1.5);
});
function updateExport() {
  const ready = !!store.get().analysis;
  const button = $('exportButton');
  button.disabled = !ready;
  button.setAttribute('aria-disabled', String(!ready));
  $('exportReason').hidden = ready;
}
store.subscribe('analysis', updateExport);
updateExport();

function setText(id, value) { $(id).textContent = value; }
function resetAnalyzeButton() { $('analyzeButton').textContent = 'Analyse passage'; }
function announce(text) {
  // Clear first so an identical repeated message is announced again.
  const region = $('liveStatus');
  region.textContent = '';
  setTimeout(() => { region.textContent = text; }, 50);
}
function renderStale() {
  const state = store.get();
  const stale = isStale(state);
  $('staleBanner').hidden = !stale;
  $('laneStack').classList.toggle('findings-stale', stale);
  if (stale) {
    const req = state.analysisRequest;
    setText('staleBanner', `Findings are from the previous passage (${formatTenths(req.start)} to ${formatTenths(req.end)}). Analyse again to update. Export the note first to keep your notes and marks.`);
  }
}
function updateSelection(start, end) {
  const next = clampSelection(start, end, player.duration);
  store.set('selection', { selection: next });
  refreshSelectionUI();
}

function refreshSelectionUI() {
  const { selection } = store.get();
  $('startTime').value = selection.start.toFixed(1);
  $('endTime').value = selection.end.toFixed(1);
  setText('selectionLabel', `${formatTime(selection.start)}–${formatTime(selection.end)} · ${(selection.end - selection.start).toFixed(1)} seconds selected`);
}

async function loadFile(chosen) {
  if (!chosen) return;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  clearTimeout(viewAnnouncement);
  transport.cancel(); eq.fileChanged();
  player.pause(); waveformLane.clear();
  const { view: emptyView, ...reset } = fileLoaded(store.get(), chosen, 0);
  store.set('ui', { ...reset, selection: { start: 0, end: 0 }, analysing: false });
  store.set('view', { view: emptyView });
  store.set('analysis', {});
  $('chordCorrection').value = ''; $('arrangementNotes').value = '';
  $('emptyState').hidden = false; $('waveWrap').hidden = true; $('overviewWrap').hidden = true;
  requestSeq += 1; // invalidate any in-flight analysis; it no longer owns the button
  resetAnalyzeButton();
  objectUrl = URL.createObjectURL(chosen);
  setText('fileName', chosen.name);
  updateExport();
  setText('selectionLabel', 'Reading audio…');
  $('analyzeButton').disabled = true;
  try {
    await new Promise((resolve, reject) => {
      player.onloadedmetadata = resolve;
      player.onerror = () => reject(new Error('The browser could not play this file.'));
      player.src = objectUrl;
    });
    if (store.get().file !== chosen) return;
    if (!Number.isFinite(player.duration) || player.duration < 1) throw new Error('The audio duration could not be read.');
    for (const id of ['playButton', 'startTime', 'endTime', 'speed', 'loop', 'analyzeButton', 'eqDisclosure']) $(id).disabled = false;
    $('startTime').max = player.duration;
    $('endTime').max = player.duration;
    $('emptyState').hidden = true; $('waveWrap').hidden = false; $('overviewWrap').hidden = false;
    setText('durationLabel', formatTime(player.duration));
    updateReadout(player.currentTime || 0);
    store.set('ui', { duration: player.duration });
    store.set('view', { view: fit(0, player.duration, player.duration) });
    updateSelection(0, Math.min(30, player.duration));
    await waveformLane.loadFile(chosen);
    if (store.get().file === chosen) renderLanes(true);
  } catch (error) { setText('selectionLabel', error.message); announce(error.message); }
}

fileInput.addEventListener('change', () => loadFile(fileInput.files[0]));
$('openButton').addEventListener('click', () => fileInput.click());
$('emptyOpen').addEventListener('click', () => fileInput.click());

// Window-level drop: any audio file loads; drops on a file input (such as the EQ import) keep their own behaviour.
const onFileInput = event => !!(event.target.closest && event.target.closest('input[type=file]'));
const hasFiles = event => [...(event.dataTransfer?.types || [])].includes('Files');
window.addEventListener('dragover', event => {
  if (onFileInput(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
  if (hasFiles(event)) document.body.classList.add('dropping');
});
window.addEventListener('dragleave', event => { if (!event.relatedTarget) document.body.classList.remove('dropping'); });
window.addEventListener('drop', event => {
  document.body.classList.remove('dropping');
  if (onFileInput(event)) return;
  event.preventDefault();
  if (!hasFiles(event)) return;
  const picked = pickAudioFile([...event.dataTransfer.files]);
  if (picked) loadFile(picked);
  else {
    const message = 'That is not an audio file. Drop an mp3, wav, flac, ogg or m4a file.';
    setText('selectionLabel', message); announce(message);
  }
});

for (const id of ['startTime', 'endTime']) $(id).addEventListener('change', () => updateSelection(Number($('startTime').value), Number($('endTime').value)));
$('analyzeButton').addEventListener('click', async () => {
  const { file, selection } = store.get();
  if (!file || selection.end - selection.start < 1) { setText('selectionLabel', 'Select at least one second.'); return; }
  const button = $('analyzeButton'); button.disabled = true; button.textContent = 'Analysing…';
  requestSeq += 1;
  // Keep analysisRequest paired with the findings still displayed. The sent
  // selection lives in requested until a successful response replaces both.
  store.set('ui', { analysing: true });
  const requested = { id: requestSeq, file, selection: { start: selection.start, end: selection.end } };
  const settle = () => settleRequest(requested, { id: requestSeq, file: store.get().file, selection: store.get().selection });
  try {
    const url = `/api/analyze?start=${encodeURIComponent(requested.selection.start)}&end=${encodeURIComponent(requested.selection.end)}`;
    const response = await fetch(url, { method: 'POST', body: requested.file, headers: { 'Content-Type': requested.file.type || 'application/octet-stream' } });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Analysis failed.');
    if (settle().stale) return;
    const previous = store.get();
    const key = reviewKey(file, result.passage);
    const different = previous.analysis && previous.review.key !== key;
    const marks = different ? Object.keys(previous.review.labels).length : 0;
    const hadNotes = different && Boolean($('chordCorrection').value || $('arrangementNotes').value);
    const review = pruneReview(reconcileReview(previous.review, key), result.instruments);
    if (different) { $('chordCorrection').value = ''; $('arrangementNotes').value = ''; }
    store.set('analysis', { analysis: result, analysisRequest: requested.selection, review, selectedBlock: null, analysing: false });
    if (marks || hadNotes) {
      const what = [marks ? `${marks} mark${marks === 1 ? '' : 's'}` : '', hadNotes ? 'your notes' : ''].filter(Boolean).join(' and ');
      announce(`${what[0].toUpperCase()}${what.slice(1)} ${marks === 1 && !hadNotes ? 'was' : 'were'} cleared for the new passage`);
    }
    setView(fit(result.passage.start_s, result.passage.end_s, store.get().duration));
  } catch (error) {
    if (!settle().stale) { setText('selectionLabel', error.message); announce(error.message); }
  } finally {
    if (settle().ownsButton) { button.disabled = false; resetAnalyzeButton(); store.set('ui', { analysing: false }); }
  }
});

$('exportButton').addEventListener('click', () => {
  const { file, analysis, review } = store.get();
  if (!analysis) return;
  const note = buildPassageNote({
    fileName: file.name, analysis, chordCorrection: $('chordCorrection').value,
    arrangementNotes: $('arrangementNotes').value, review, exportedAt: new Date().toISOString(), eq: eq.snapshot(),
  });
  const url = URL.createObjectURL(new Blob([JSON.stringify(note, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'ohrin-passage-note.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
initTheme();
observeCanvasHost(canvas);
observeCanvasHost($('energyChart'));
observeCanvasHost($('overview'));
observeCanvasHost($('ruler'));
onInvalidate(() => renderLanes());

// Speed, EQ, Export and Theme sit in the top bar from 1280 px and inside More below. One set of nodes moves between the two.
const wideQuery = window.matchMedia('(min-width: 1280px)');
function placeTools() {
  const wide = wideQuery.matches;
  const group = $('toolGroup');
  const target = wide ? $('toolSlot') : $('moreBody');
  if (group.parentElement !== target) target.append(group);
  $('topBar').classList.toggle('wide', wide);
  if (wide) $('moreMenu').open = false;
}
wideQuery.addEventListener('change', placeTools);
placeTools();
const more = $('moreMenu');
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && more.open) { more.open = false; more.querySelector('summary').focus(); }
});
document.addEventListener('click', event => {
  if (!more.open) return;
  // Close on an outside click and after EQ or Export, so the opened panel or download is not hidden behind the menu.
  if (!more.contains(event.target) || event.target.closest('#eqDisclosure, #exportButton')) {
    const inside = more.contains(event.target); more.open = false;
    if (inside) more.querySelector('summary').focus();
  }
});
