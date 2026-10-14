import { clampSelection, shouldLoop, shouldStop, formatTime, measuredObservations, timeToFraction, instrumentTimeline,
  emptyReview, reviewKey, candidateTarget, segmentTarget, setLabel, reconcileReview, pruneReview, reviewSummary,
  settleRequest, dragExceeded, uncoveredCaption, reviewTallyText, buildPassageNote } from './state.mjs';

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
let dragStartX = 0;
let dragging = false;
let requestSeq = 0;
let rovingSegment = 0;
let analysis = null;
let review = emptyReview();
let selectedSegment = null;

function setText(id, value) { $(id).textContent = value; }
function setTextIfChanged(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }
function resetAnalyzeButton() { $('analyzeButton').innerHTML = 'Analyse passage <span>↗</span>'; }
function announce(text) { setTextIfChanged('liveStatus', text); }
function clearPassageFindings() {
  analysis = null;
  review = emptyReview();
  selectedSegment = null;
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
  requestSeq += 1; // invalidate any in-flight analysis; it no longer owns the button
  resetAnalyzeButton();
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
  dragStart = timeAtPointer(event); dragStartX = event.clientX; dragging = false;
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener('pointermove', event => {
  if (dragStart === null) return;
  if (!dragging && dragExceeded(dragStartX, event.clientX)) dragging = true;
  if (dragging) updateSelection(dragStart, timeAtPointer(event));
});
canvas.addEventListener('pointerup', event => {
  if (dragStart === null) return;
  const end = timeAtPointer(event);
  if (dragging) updateSelection(dragStart, end);
  else player.currentTime = end; // a click, not a drag: seek only, keep the selection and findings
  dragStart = null; dragging = false;
});
canvas.addEventListener('pointercancel', () => { dragStart = null; dragging = false; });
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
    const x = left + timeToFraction((v.start_s + v.end_s) / 2, analysis.passage) * chartW;
    const y = top + Math.max(0, Math.min(1, -v.rms_dbfs / 40)) * (bottom - top);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = '#9fe4ca'; ctx.lineWidth = 2.5; ctx.stroke();
  ctx.fillStyle = '#839b94'; ctx.fillText(formatTime(analysis.passage.start_s), left, h - 4);
  ctx.fillText(formatTime(analysis.passage.end_s), w - 39, h - 4);
}

const MARK_GLYPH = { heard: '✓', not_heard: '✕', unsure: '?' };
const MARK_TEXT = { heard: 'I hear it', not_heard: "I don't hear it", unsure: 'Unsure' };
const CHOICES = [['heard', 'I hear it'], ['not_heard', "I don't hear it"], ['unsure', 'Unsure']];

function reviewFieldset(target, group, legend) {
  const set = document.createElement('fieldset'); set.className = 'review-fields';
  const lg = document.createElement('legend'); lg.className = 'visually-hidden'; lg.textContent = legend; set.append(lg);
  for (const [value, text] of CHOICES) {
    const label = document.createElement('label');
    const input = document.createElement('input'); input.type = 'radio'; input.name = group; input.value = value;
    input.checked = review.labels[target] === value;
    input.dataset.focusId = `${group}-${value}`;
    input.addEventListener('change', () => { review = setLabel(review, target, value); renderReview(); announceTally(); });
    label.append(input, ' ', text); set.append(label);
  }
  const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'review-clear'; clear.textContent = 'Clear';
  clear.dataset.focusId = `${group}-clear`;
  clear.setAttribute('aria-label', `Clear mark: ${legend}`);
  clear.addEventListener('click', () => { review = setLabel(review, target, null); renderReview(); announceTally(); });
  set.append(clear);
  return set;
}

function announceTally() {
  const info = analysis?.instruments;
  if (info?.available) announce(reviewTallyText(info.classes, review));
}

function renderReview() {
  const focusId = document.activeElement?.dataset?.focusId;
  const info = analysis?.instruments;
  const available = Boolean(info && info.available);
  $('reviewWrap').hidden = !available;
  const inst = $('instrumentCandidates');
  if (available) {
    inst.replaceChildren();
    for (const c of info.classes) {
      addCandidate(inst, c.name, `score ${c.probability} · ${c.above_threshold ? 'above' : 'below'} tuned threshold ${c.threshold} · uncertain`);
      inst.lastElementChild.append(reviewFieldset(candidateTarget(c.name), `review-class-${c.name}`, `Do you hear ${c.name} in this passage?`));
    }
    setTextIfChanged('reviewTally', reviewTallyText(info.classes, review));
  }
  drawTimeline();
  const box = $('segmentReview');
  box.replaceChildren();
  const seg = available && selectedSegment !== null ? info.segments[selectedSegment] : null;
  box.hidden = !available || !$('instrumentTimeline') || $('instrumentTimeline').hidden;
  if (!box.hidden) {
    if (!seg) {
      const p = document.createElement('p'); p.className = 'subtle'; p.textContent = 'Select a block in the timeline to mark what you hear in it.'; box.append(p);
    } else {
      const h = document.createElement('h4');
      h.textContent = `Segment ${formatTime(seg.start_s)}–${formatTime(seg.end_s)} (≈${(seg.end_s - seg.start_s).toFixed(1)} s block)`;
      const play = document.createElement('button'); play.type = 'button'; play.className = 'secondary-button'; play.textContent = 'Play this block';
      play.dataset.focusId = 'seg-play';
      play.addEventListener('click', () => { player.currentTime = seg.start_s; player.play(); });
      box.append(h, play);
      for (const c of info.classes) {
        const wrap = document.createElement('div'); wrap.className = 'segment-class';
        const p = seg.probabilities?.[c.name];
        const strong = document.createElement('strong'); strong.textContent = `${c.name} · model score ${typeof p === 'number' ? p.toFixed(2) : 'n/a'}`;
        wrap.append(strong, reviewFieldset(segmentTarget(selectedSegment, c.name), `review-seg-${selectedSegment}-${c.name}`, `Do you hear ${c.name} in this block?`));
        box.append(wrap);
      }
    }
  }
  if (focusId) document.querySelector(`[data-focus-id="${CSS.escape(focusId)}"]`)?.focus();
}

const pct = f => `${(f * 100).toFixed(3)}%`;
function fmtScore(p) { return p === null ? 'n/a' : p.toFixed(2); }

function drawTimeline() {
  const box = $('instrumentTimeline');
  const rowsEl = $('timelineRows');
  const table = $('timelineTable');
  rowsEl.replaceChildren();
  $('blockStrip').replaceChildren();
  table.tHead.replaceChildren();
  table.tBodies[0].replaceChildren();
  const tl = analysis ? instrumentTimeline(analysis.instruments, analysis.passage) : null;
  box.hidden = !tl;
  if (!tl) return;
  for (const row of tl.rows) {
    const label = document.createElement('div'); label.className = 'timeline-label'; label.setAttribute('aria-hidden', 'true');
    label.textContent = `${row.name} · threshold ${row.threshold}`;
    const track = document.createElement('div'); track.className = 'timeline-track';
    for (const u of tl.uncovered) {
      const gap = document.createElement('div'); gap.className = 'tl-uncovered'; gap.setAttribute('aria-hidden', 'true');
      const l = timeToFraction(u.start_s, analysis.passage);
      gap.style.left = pct(l); gap.style.width = pct(timeToFraction(u.end_s, analysis.passage) - l);
      gap.textContent = 'not scored'; gap.title = `${formatTime(u.start_s)}–${formatTime(u.end_s)} not scored`;
      track.append(gap);
    }
    for (const c of row.cells) {
      const mark = review.labels[segmentTarget(c.index, row.name)];
      const cell = document.createElement('div');
      cell.className = `tl-cell ${c.above ? 'above' : 'below'}${c.short ? ' short' : ''}${selectedSegment === c.index ? ' selected' : ''}`;
      cell.setAttribute('aria-hidden', 'true'); // mouse convenience only; keyboard and AT use the block strip below
      cell.addEventListener('click', () => selectSegment(c.index));
      cell.style.left = pct(c.left); cell.style.width = pct(c.width);
      cell.title = `${row.name} ${formatTime(c.start_s)}–${formatTime(c.end_s)} · score ${fmtScore(c.probability)} · ${c.above ? 'above' : 'below'} threshold ${row.threshold} · model output${mark ? ` · your mark: ${MARK_TEXT[mark]}` : ''}`;
      if (mark) { const g = document.createElement('span'); g.className = 'tl-glyph'; g.setAttribute('aria-hidden', 'true'); g.textContent = MARK_GLYPH[mark]; cell.append(g); }
      const fill = document.createElement('div'); fill.className = 'tl-fill'; fill.setAttribute('aria-hidden', 'true');
      fill.style.height = pct(c.probability === null ? 0 : Math.max(0, Math.min(1, c.probability)));
      cell.append(fill); track.append(cell);
    }
    const marker = document.createElement('div'); marker.className = 'tl-threshold';
    marker.style.bottom = pct(Math.max(0, Math.min(1, row.threshold)));
    track.append(marker);
    rowsEl.append(label, track);
  }
  drawBlockStrip(tl);
  const headRow = table.tHead.insertRow();
  for (const text of ['Time', ...tl.rows.map(r => r.name)]) {
    const th = document.createElement('th'); th.scope = 'col'; th.textContent = text; headRow.append(th);
  }
  const first = tl.rows[0];
  first.cells.forEach((_, i) => {
    const tr = table.tBodies[0].insertRow();
    tr.insertCell().textContent = `${formatTime(first.cells[i].start_s)}–${formatTime(first.cells[i].end_s)}`;
    for (const row of tl.rows) {
      const c = row.cells[i];
      tr.insertCell().textContent = `${fmtScore(c.probability)} (${c.above ? 'above' : 'below'})`;
    }
  });
  const parts = [`Each block averages about ${tl.resolution_s} s of audio (ten 0.96 s VGGish frames), so changes inside a block are invisible and block edges are not entry or exit times. Scores come from an OpenMIC-trained head evaluated on 10 s clips, not on full mixes.`];
  if (tl.shortCount) parts.push(`${tl.shortCount} short block${tl.shortCount === 1 ? ' is' : 's are'} outside the training condition.`);
  const gaps = uncoveredCaption(tl.uncovered);
  if (gaps) parts.push(gaps);
  if (first.cells.length === 1) parts.push('Only one block: no change over time can be shown.');
  setTextIfChanged('timelineCaption', parts.join(' '));
}

function selectSegment(index) {
  selectedSegment = index; rovingSegment = index;
  renderReview();
  const seg = analysis?.instruments?.segments?.[index];
  if (seg) announce(`Block ${index + 1}, ${formatTime(seg.start_s)} to ${formatTime(seg.end_s)} selected. Marking controls are below the timeline.`);
}

// One real button per block (not per class row), a single tab stop with arrow keys.
function drawBlockStrip(tl) {
  const strip = $('blockStrip');
  strip.replaceChildren();
  const count = tl.rows[0].cells.length;
  if (rovingSegment >= count) rovingSegment = 0;
  const active = selectedSegment ?? rovingSegment;
  const buttons = [];
  tl.rows[0].cells.forEach((c, i) => {
    const marks = tl.rows.map(r => review.labels[segmentTarget(i, r.name)] && `${r.name}: ${MARK_TEXT[review.labels[segmentTarget(i, r.name)]]}`).filter(Boolean);
    const b = document.createElement('button'); b.type = 'button'; b.className = `tl-block${selectedSegment === i ? ' selected' : ''}`;
    b.dataset.focusId = `seg-${i}`;
    b.tabIndex = i === active ? 0 : -1;
    b.setAttribute('aria-controls', 'segmentReview');
    if (selectedSegment === i) b.setAttribute('aria-current', 'true');
    b.setAttribute('aria-label', `Block ${i + 1} of ${count}, ${formatTime(c.start_s)} to ${formatTime(c.end_s)}${marks.length ? `. Your marks: ${marks.join(', ')}` : ''}`);
    b.textContent = String(i + 1);
    b.style.left = pct(c.left); b.style.width = pct(c.width);
    b.addEventListener('click', () => selectSegment(i));
    buttons.push(b); strip.append(b);
  });
  strip.onkeydown = event => {
    const cur = buttons.indexOf(document.activeElement);
    if (cur < 0) return;
    const next = { ArrowRight: cur + 1, ArrowDown: cur + 1, ArrowLeft: cur - 1, ArrowUp: cur - 1, Home: 0, End: buttons.length - 1 }[event.key];
    if (next === undefined) return;
    event.preventDefault();
    const target = buttons[Math.max(0, Math.min(buttons.length - 1, next))];
    rovingSegment = buttons.indexOf(target);
    buttons.forEach(b => { b.tabIndex = b === target ? 0 : -1; });
    target.focus();
  };
}

function addCandidate(container, title, detail) {
  const item = document.createElement('div'); item.className = 'candidate';
  const strong = document.createElement('strong'); strong.textContent = title;
  const small = document.createElement('small'); small.textContent = detail;
  item.append(strong, small); container.append(item);
}

function showAnalysis() {
  $('results').hidden = false;
  $('timelineRows').replaceChildren(); $('timelineTable').tBodies[0].replaceChildren();
  $('segmentReview').replaceChildren(); selectedSegment = null;
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
    setText('instrumentCaveat', `${info.caveat} Segments: ${info.segments.length}.`);
  } else {
    inst.textContent = 'Instrument model unavailable.';
    setText('instrumentCaveat', info ? info.reason : 'Not requested.');
  }
  const strongest = remarks[0] || 'Compare the first and last part of the selection.';
  setText('creativeIdea', `Try rebuilding the passage with the same overall level contrast. ${strongest} Change the timing or the layer that carries it to make the idea your own.`);
  setText('energyCaption', 'RMS dBFS from the mixed recording. Higher on the chart means louder; this is not an instrument stem.');
  drawEnergy(); renderReview(); $('results').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

$('analyzeButton').addEventListener('click', async () => {
  if (!file || selection.end - selection.start < 1) { setText('selectionLabel', 'Select at least one second.'); return; }
  const button = $('analyzeButton'); button.disabled = true; button.textContent = 'Analysing…';
  requestSeq += 1;
  const requested = { id: requestSeq, file, selection: { start: selection.start, end: selection.end } };
  const settle = () => settleRequest(requested, { id: requestSeq, file, selection });
  try {
    const url = `/api/analyze?start=${encodeURIComponent(requested.selection.start)}&end=${encodeURIComponent(requested.selection.end)}`;
    const response = await fetch(url, { method: 'POST', body: requested.file, headers: { 'Content-Type': requested.file.type || 'application/octet-stream' } });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Analysis failed.');
    if (settle().stale) return;
    analysis = result;
    review = pruneReview(reconcileReview(review, reviewKey(file, result.passage)), result.instruments);
    showAnalysis();
  } catch (error) {
    if (!settle().stale) setText('selectionLabel', error.message);
  } finally {
    if (settle().ownsButton) { button.disabled = false; resetAnalyzeButton(); }
  }
});

$('exportButton').addEventListener('click', () => {
  if (!analysis) return;
  const note = buildPassageNote({
    fileName: file.name, analysis, chordCorrection: $('chordCorrection').value,
    arrangementNotes: $('arrangementNotes').value, review, exportedAt: new Date().toISOString(),
  });
  const url = URL.createObjectURL(new Blob([JSON.stringify(note, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = 'ohrin-passage-note.json'; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('resize', () => { drawWaveform(); if (analysis) { drawEnergy(); renderReview(); } });
drawWaveform();
