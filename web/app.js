import { initEq } from './eq-ui.js';
import { initTheme, getPalette, onInvalidate, observeCanvasHost } from './theme.mjs';
import { createStore, fileLoaded } from './store.mjs';
import { createTransport } from './transport.js';
import { createInspector } from './inspector.js';
import { formatTenths } from './timeline.mjs';
import { pickAudioFile } from './input-rules.mjs';
import { clampSelection, formatTime, emptyReview, reviewKey, reconcileReview, pruneReview,
  settleRequest, dragExceeded, isPrimaryPress, primaryButtonHeld, resolveDrag, buildPassageNote } from './state.mjs';

const $ = id => document.getElementById(id);
const fileInput = $('audioFile');
const player = $('player');
const canvas = $('waveform');
const store = createStore();
let objectUrl = null;
let waveform = null;
let dragStart = null;
let dragStartX = 0;
let dragging = false;
let requestSeq = 0;
const eq = initEq({ player, announce, fitCanvas });
const transport = createTransport({ player, store, announce, resume: () => eq.resume(), isLooping: () => $('loop').checked });
transport.bindControls({ playButton: $('playButton'), speed: $('speed') });
const inspector = createInspector({ store, announce, transport, fitCanvas });
store.subscribe('analysis', () => inspector.render('analysis'));
store.subscribe('review', () => inspector.render('review'));
const ICON_PLAY = '<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M232.4,114.49,88.32,26.35a16,16,0,0,0-16.2-.3A15.86,15.86,0,0,0,64,39.87V216.13A15.94,15.94,0,0,0,80,232a16.07,16.07,0,0,0,8.36-2.35L232.4,141.51a15.81,15.81,0,0,0,0-27ZM80,215.94V40l143.83,88Z"/></svg>';
const ICON_PAUSE = '<svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path d="M200,32H160a16,16,0,0,0-16,16V208a16,16,0,0,0,16,16h40a16,16,0,0,0,16-16V48A16,16,0,0,0,200,32Zm0,176H160V48h40ZM96,32H56A16,16,0,0,0,40,48V208a16,16,0,0,0,16,16H96a16,16,0,0,0,16-16V48A16,16,0,0,0,96,32Zm0,176H56V48H96Z"/></svg>';
// Registered after transport.bindControls, so these icons replace its text glyphs.
player.addEventListener('play', () => { $('playButton').innerHTML = ICON_PAUSE; });
player.addEventListener('pause', () => { $('playButton').innerHTML = ICON_PLAY; });
function updateReadout(t) {
  const d = Number.isFinite(player.duration) ? player.duration : 0;
  setText('timeReadout', `${formatTenths(t)} / ${formatTenths(d)}`);
}
transport.onFrame(t => { updateReadout(t); drawWaveform(); });
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
function clearPassageFindings() {
  store.set('analysis', { analysis: null, analysisRequest: null, review: emptyReview(), selectedBlock: null });
  $('results').hidden = true;
  $('chordCorrection').value = '';
  $('arrangementNotes').value = '';
}
function fitCanvas(target) {
  const ratio = window.devicePixelRatio || 1;
  const box = target.getBoundingClientRect();
  const w = Math.max(1, box.width);
  const h = Math.max(1, box.height);
  target.width = Math.round(w * ratio);
  target.height = Math.round(h * ratio);
  const ctx = target.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, w, h };
}

function drawWaveform() {
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const p = getPalette();
  ctx.strokeStyle = p.grid;
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  if (!waveform) return;
  const d = Math.max(player.duration || 1, 1);
  const { selection } = store.get();
  const x1 = selection.start / d * w;
  const x2 = selection.end / d * w;
  ctx.fillStyle = p.accentFill; ctx.fillRect(x1, 0, Math.max(1, x2 - x1), h);
  const step = waveform.length / w;
  for (let x = 0; x < w; x++) {
    const amp = waveform[Math.min(waveform.length - 1, Math.floor(x * step))] || 0;
    ctx.strokeStyle = x >= x1 && x <= x2 ? p.wave : p.waveDim;
    ctx.beginPath(); ctx.moveTo(x + .5, h / 2 - amp * h * .43); ctx.lineTo(x + .5, h / 2 + amp * h * .43); ctx.stroke();
  }
  ctx.fillStyle = p.playhead;
  ctx.fillRect((player.currentTime || 0) / d * w - 1, 0, 2, h);
  for (const x of [x1, x2]) { ctx.fillStyle = p.accent; ctx.fillRect(x - 1, 0, 2, h); }
}

async function makeWaveform(audioFile) {
  const context = new AudioContext();
  try {
    const decoded = await context.decodeAudioData(await audioFile.arrayBuffer());
    const samples = decoded.getChannelData(0);
    const bins = 1800;
    const step = Math.max(1, Math.floor(samples.length / bins));
    const peaks = new Float32Array(bins);
    for (let i = 0; i < bins; i++) {
      let max = 0;
      for (let j = i * step; j < Math.min(samples.length, (i + 1) * step); j += 4) max = Math.max(max, Math.abs(samples[j]));
      peaks[i] = max;
    }
    return peaks;
  } finally { await context.close(); }
}

function updateSelection(start, end) {
  const { selection } = store.get();
  const next = clampSelection(start, end, player.duration);
  if (next.start !== selection.start || next.end !== selection.end) clearPassageFindings();
  store.set('selection', { selection: next });
  refreshSelectionUI();
}

function refreshSelectionUI() {
  const { selection } = store.get();
  $('startTime').value = selection.start.toFixed(1);
  $('endTime').value = selection.end.toFixed(1);
  setText('selectionLabel', `${formatTime(selection.start)}–${formatTime(selection.end)} · ${(selection.end - selection.start).toFixed(1)} seconds selected`);
  drawWaveform();
}

async function loadFile(chosen) {
  if (!chosen) return;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  transport.cancel(); eq.fileChanged();
  player.pause(); store.set('ui', { file: chosen }); waveform = null;
  requestSeq += 1; // invalidate any in-flight analysis; it no longer owns the button
  resetAnalyzeButton();
  clearPassageFindings();
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
    if (!Number.isFinite(player.duration) || player.duration < 1) throw new Error('The audio duration could not be read.');
    for (const id of ['playButton', 'startTime', 'endTime', 'speed', 'loop', 'analyzeButton', 'eqDisclosure']) $(id).disabled = false;
    $('startTime').max = player.duration;
    $('endTime').max = player.duration;
    $('emptyState').hidden = true; $('waveWrap').hidden = false;
    setText('durationLabel', formatTime(player.duration));
    updateReadout(player.currentTime || 0);
    store.set('view', fileLoaded(store.get(), chosen, player.duration));
    updateSelection(0, Math.min(30, player.duration));
    waveform = await makeWaveform(chosen);
    drawWaveform();
  } catch (error) { setText('selectionLabel', error.message); }
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
  if (onFileInput(event)) return;
  event.preventDefault();
  document.body.classList.remove('dropping');
  if (!hasFiles(event)) return;
  const picked = pickAudioFile([...event.dataTransfer.files]);
  if (picked) loadFile(picked);
  else {
    const message = 'That is not an audio file. Drop an mp3, wav, flac, ogg or m4a file.';
    setText('selectionLabel', message); announce(message);
  }
});

function timeAtPointer(event) {
  const box = canvas.getBoundingClientRect();
  return Math.max(0, Math.min(player.duration, (event.clientX - box.left) / box.width * player.duration));
}
let dragPointerId = null;
let selectionBeforeDrag = null;
function endDrag() { dragStart = null; dragging = false; dragPointerId = null; selectionBeforeDrag = null; }
function abandonDrag() {
  // Put back the selection from before the drag; findings were never touched during the preview.
  if (selectionBeforeDrag) { store.set('selection', { selection: selectionBeforeDrag }); refreshSelectionUI(); }
  endDrag();
}
canvas.addEventListener('pointerdown', event => {
  if (!store.get().file || !Number.isFinite(player.duration)) return;
  if (!isPrimaryPress(event)) return;
  dragStart = timeAtPointer(event); dragStartX = event.clientX; dragging = false;
  dragPointerId = event.pointerId; selectionBeforeDrag = { ...store.get().selection };
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener('pointermove', event => {
  if (dragStart === null) return;
  if (event.pointerId !== dragPointerId || !primaryButtonHeld(event)) { abandonDrag(); return; }
  if (!dragging && dragExceeded(dragStartX, event.clientX)) dragging = true;
  // Preview only: findings are cleared on commit, not while moving.
  if (dragging) { store.set('selection', { selection: clampSelection(dragStart, timeAtPointer(event), player.duration) }); refreshSelectionUI(); }
});
canvas.addEventListener('pointerup', event => {
  if (dragStart === null || event.pointerId !== dragPointerId) return;
  const end = timeAtPointer(event);
  const outcome = resolveDrag({ startX: dragStartX, endX: event.clientX, startS: dragStart, endS: end });
  const previous = selectionBeforeDrag, from = dragStart;
  endDrag();
  if (outcome === 'commit') {
    store.set('selection', { selection: previous }); // so updateSelection sees the change and clears stale findings once
    updateSelection(from, end);
  } else {
    store.set('selection', { selection: previous }); refreshSelectionUI();
    if (event.button === 0) transport.seek(end); // a click: seek only, keep selection and findings
  }
});
canvas.addEventListener('pointercancel', abandonDrag);
canvas.addEventListener('lostpointercapture', abandonDrag);
for (const id of ['startTime', 'endTime']) $(id).addEventListener('change', () => updateSelection(Number($('startTime').value), Number($('endTime').value)));
$('analyzeButton').addEventListener('click', async () => {
  const { file, selection } = store.get();
  if (!file || selection.end - selection.start < 1) { setText('selectionLabel', 'Select at least one second.'); return; }
  const button = $('analyzeButton'); button.disabled = true; button.textContent = 'Analysing…';
  requestSeq += 1;
  store.set('ui', { analysisRequest: { start: selection.start, end: selection.end } });
  const requested = { id: requestSeq, file, selection: { start: selection.start, end: selection.end } };
  const settle = () => settleRequest(requested, { id: requestSeq, file: store.get().file, selection: store.get().selection });
  try {
    const url = `/api/analyze?start=${encodeURIComponent(requested.selection.start)}&end=${encodeURIComponent(requested.selection.end)}`;
    const response = await fetch(url, { method: 'POST', body: requested.file, headers: { 'Content-Type': requested.file.type || 'application/octet-stream' } });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Analysis failed.');
    if (settle().stale) return;
    const review = pruneReview(reconcileReview(store.get().review, reviewKey(file, result.passage)), result.instruments);
    store.set('analysis', { analysis: result, review, selectedBlock: null });
  } catch (error) {
    if (!settle().stale) setText('selectionLabel', error.message);
  } finally {
    if (settle().ownsButton) { button.disabled = false; resetAnalyzeButton(); }
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
onInvalidate(() => { drawWaveform(); inspector.render('layout'); });
drawWaveform();

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
  if (!more.contains(event.target) || event.target.closest('#eqDisclosure, #exportButton')) more.open = false;
});
