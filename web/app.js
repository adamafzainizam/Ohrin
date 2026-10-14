import { clampSelection, shouldLoop, shouldStop, formatTime, measuredObservations } from './state.mjs';

const $ = id => document.getElementById(id);
const fileInput = $('audioFile');
const player = $('player');
const canvas = $('waveform');
const energy = $('energyChart');
let file = null;
let objectUrl = null;
let waveform = null;
let selection = { start: 0, end: 0 };
let dragStart = null;
let analysis = null;

function setText(id, value) { $(id).textContent = value; }
function clearPassageFindings() {
  analysis = null;
  $('results').hidden = true;
  $('chordCorrection').value = '';
  $('arrangementNotes').value = '';
}
function fitCanvas(target) {
  const ratio = window.devicePixelRatio || 1;
  const w = Math.max(1, target.clientWidth);
  const h = Math.max(1, target.clientHeight);
  target.width = Math.round(w * ratio);
  target.height = Math.round(h * ratio);
  const ctx = target.getContext('2d');
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, w, h };
}

function drawWaveform() {
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  ctx.strokeStyle = '#263a3a';
  ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
  if (!waveform) return;
  const d = Math.max(player.duration || 1, 1);
  const x1 = selection.start / d * w;
  const x2 = selection.end / d * w;
  ctx.fillStyle = 'rgba(159,228,202,.13)'; ctx.fillRect(x1, 0, Math.max(1, x2 - x1), h);
  const step = waveform.length / w;
  for (let x = 0; x < w; x++) {
    const amp = waveform[Math.min(waveform.length - 1, Math.floor(x * step))] || 0;
    ctx.strokeStyle = x >= x1 && x <= x2 ? '#9fe4ca' : '#4d7771';
    ctx.beginPath(); ctx.moveTo(x + .5, h / 2 - amp * h * .43); ctx.lineTo(x + .5, h / 2 + amp * h * .43); ctx.stroke();
  }
  ctx.fillStyle = '#efbb7a';
  ctx.fillRect((player.currentTime || 0) / d * w - 1, 0, 2, h);
  for (const x of [x1, x2]) { ctx.fillStyle = '#a9e6d0'; ctx.fillRect(x - 1, 0, 2, h); }
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
  const next = clampSelection(start, end, player.duration);
  if (next.start !== selection.start || next.end !== selection.end) clearPassageFindings();
  selection = next;
  $('startTime').value = selection.start.toFixed(1);
  $('endTime').value = selection.end.toFixed(1);
  setText('selectionLabel', `${formatTime(selection.start)}–${formatTime(selection.end)} · ${(selection.end - selection.start).toFixed(1)} seconds selected`);
  drawWaveform();
}

async function loadFile(chosen) {
  if (!chosen) return;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  player.pause(); file = chosen; waveform = null;
  clearPassageFindings();
  objectUrl = URL.createObjectURL(chosen);
  setText('fileName', chosen.name);
  setText('selectionLabel', 'Reading audio…');
  $('analyzeButton').disabled = true;
  try {
    await new Promise((resolve, reject) => {
      player.onloadedmetadata = resolve;
      player.onerror = () => reject(new Error('The browser could not play this file.'));
      player.src = objectUrl;
    });
    if (!Number.isFinite(player.duration) || player.duration < 1) throw new Error('The audio duration could not be read.');
    for (const id of ['playButton', 'startTime', 'endTime', 'speed', 'loop', 'analyzeButton']) $(id).disabled = false;
    $('startTime').max = player.duration;
    $('endTime').max = player.duration;
    setText('durationLabel', formatTime(player.duration));
    updateSelection(0, Math.min(30, player.duration));
    waveform = await makeWaveform(chosen);
    drawWaveform();
  } catch (error) { setText('selectionLabel', error.message); }
}

fileInput.addEventListener('change', () => loadFile(fileInput.files[0]));
const dropZone = $('dropZone');
for (const type of ['dragenter', 'dragover']) dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.add('dragging'); });
for (const type of ['dragleave', 'drop']) dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.remove('dragging'); });
dropZone.addEventListener('drop', event => loadFile(event.dataTransfer.files[0]));

function timeAtPointer(event) {
  const box = canvas.getBoundingClientRect();
  return Math.max(0, Math.min(player.duration, (event.clientX - box.left) / box.width * player.duration));
}
canvas.addEventListener('pointerdown', event => {
  if (!file || !Number.isFinite(player.duration)) return;
  dragStart = timeAtPointer(event); canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener('pointermove', event => { if (dragStart !== null) updateSelection(dragStart, timeAtPointer(event)); });
canvas.addEventListener('pointerup', event => {
  if (dragStart === null) return;
  const end = timeAtPointer(event);
  if (Math.abs(end - dragStart) < .3) player.currentTime = end;
  else updateSelection(dragStart, end);
  dragStart = null;
});
for (const id of ['startTime', 'endTime']) $(id).addEventListener('change', () => updateSelection(Number($('startTime').value), Number($('endTime').value)));
$('speed').addEventListener('change', () => { player.playbackRate = Number($('speed').value); player.preservesPitch = true; });
$('playButton').addEventListener('click', () => {
  if (player.paused) { if (player.currentTime >= selection.end || player.currentTime < selection.start) player.currentTime = selection.start; player.play(); }
  else player.pause();
});
player.addEventListener('play', () => { $('playButton').textContent = 'Ⅱ'; });
player.addEventListener('pause', () => { $('playButton').textContent = '▶'; });
player.addEventListener('timeupdate', () => {
  if (shouldLoop(player.currentTime, selection, $('loop').checked)) player.currentTime = selection.start;
  else if (shouldStop(player.currentTime, selection, $('loop').checked)) { player.pause(); player.currentTime = selection.end; }
  setText('timeLabel', formatTime(player.currentTime));
  drawWaveform();
});

function drawEnergy() {
  const { ctx, w, h } = fitCanvas(energy);
  const values = analysis.windows;
  ctx.clearRect(0, 0, w, h);
  const left = 32, bottom = h - 24, top = 12, chartW = w - 44;
  for (const db of [-40, -30, -20, -10, 0]) {
    const y = top + (-db / 40) * (bottom - top);
    ctx.strokeStyle = '#304044'; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(w - 8, y); ctx.stroke();
    ctx.fillStyle = '#839b94'; ctx.font = '10px system-ui'; ctx.fillText(String(db), 2, y + 3);
  }
  if (!values.length) return;
  ctx.beginPath();
  values.forEach((v, i) => {
    const x = left + i / Math.max(values.length - 1, 1) * chartW;
    const y = top + Math.max(0, Math.min(1, -v.rms_dbfs / 40)) * (bottom - top);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#9fe4ca'; ctx.lineWidth = 2.5; ctx.stroke();
  ctx.fillStyle = '#839b94'; ctx.fillText(formatTime(analysis.passage.start_s), left, h - 4);
  ctx.fillText(formatTime(analysis.passage.end_s), w - 39, h - 4);
}

function addCandidate(container, title, detail) {
  const item = document.createElement('div'); item.className = 'candidate';
  const strong = document.createElement('strong'); strong.textContent = title;
  const small = document.createElement('small'); small.textContent = detail;
  item.append(strong, small); container.append(item);
}

function showAnalysis() {
  $('results').hidden = false;
  const remarks = measuredObservations(analysis.windows);
  const list = $('observations'); list.replaceChildren();
  for (const remark of remarks.length ? remarks : ['No one-second level drop over 3 dB was found in this selection.']) {
    const li = document.createElement('li'); li.textContent = remark; list.append(li);
  }
  const tempo = $('tempoCandidates'); tempo.replaceChildren();
  for (const item of analysis.tempo_candidates) addCandidate(tempo, `${item.bpm} BPM`, `periodicity ${item.periodicity} · tentative`);
  if (!analysis.tempo_candidates.length) tempo.textContent = 'No stable pulse candidate for this selection.';
  const chords = $('chordCandidates'); chords.replaceChildren();
  for (const item of analysis.candidate_chords) addCandidate(chords, item.name, `frets ${item.suggested_standard_tuning_frets.join('–')} · match ${item.score}`);
  if (!analysis.candidate_chords.length) chords.textContent = 'No useful harmonic sketch found.';
  const inst = $('instrumentCandidates'); inst.replaceChildren();
  const info = analysis.instruments;
  if (info && info.available) {
    for (const c of info.classes) addCandidate(inst, c.name, `score ${c.probability} · ${c.above_threshold ? 'above' : 'below'} tuned threshold ${c.threshold} · uncertain`);
    setText('instrumentCaveat', `${info.caveat} Segments: ${info.segments.length}.`);
  } else {
    inst.textContent = 'Instrument model unavailable.';
    setText('instrumentCaveat', info ? info.reason : 'Not requested.');
  }
  const strongest = remarks[0] || 'Compare the first and last part of the selection.';
  setText('creativeIdea', `Try rebuilding the passage with the same overall level contrast. ${strongest} Change the timing or the layer that carries it to make the idea your own.`);
  setText('energyCaption', 'RMS dBFS from the mixed recording. Higher on the chart means louder; this is not an instrument stem.');
  drawEnergy(); $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('analyzeButton').addEventListener('click', async () => {
  if (!file || selection.end - selection.start < 1) { setText('selectionLabel', 'Select at least one second.'); return; }
  const button = $('analyzeButton'); button.disabled = true; button.textContent = 'Analysing…';
  try {
    const url = `/api/analyze?start=${encodeURIComponent(selection.start)}&end=${encodeURIComponent(selection.end)}`;
    const response = await fetch(url, { method: 'POST', body: file, headers: { 'Content-Type': file.type || 'application/octet-stream' } });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Analysis failed.');
    analysis = result; showAnalysis();
  } catch (error) { setText('selectionLabel', error.message); }
  finally { button.disabled = false; button.innerHTML = 'Analyse passage <span>↗</span>'; }
});

$('exportButton').addEventListener('click', () => {
  if (!analysis) return;
  const note = {
    app: 'Ohrin', file_name: file.name, passage: analysis.passage,
    measured_observations: measuredObservations(analysis.windows),
    tempo_candidates: analysis.tempo_candidates, harmonic_candidates: analysis.candidate_chords,
    chord_correction: $('chordCorrection').value, arrangement_notes: $('arrangementNotes').value,
    instrument_candidates: analysis.instruments,
    limitations: analysis.limitations,
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(note, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'ohrin-passage-note.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('resize', () => { drawWaveform(); if (analysis) drawEnergy(); });
drawWaveform();
