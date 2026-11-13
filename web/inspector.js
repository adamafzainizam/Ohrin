import { measuredObservations, timeToFraction, instrumentTimeline, candidateTarget, segmentTarget, setLabel,
  uncoveredCaption, reviewTallyText, formatTime } from './state.mjs';

const $ = id => document.getElementById(id);
function setText(id, value) { $(id).textContent = value; }
function setTextIfChanged(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }

// Results panel: analysis summary, instrument candidates, block timeline and review controls.
// render('analysis') shows a new analysis, render('review') redraws marks and the selected block,
// render('layout') redraws canvases after a resize.
export function createInspector({ store, announce, transport, fitCanvas }) {
  let rovingSegment = 0;
  function drawEnergy() {
    const { analysis } = store.get();
    const { ctx, w, h } = fitCanvas($('energyChart'));
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
      input.checked = store.get().review.labels[target] === value;
      input.dataset.focusId = `${group}-${value}`;
      input.addEventListener('change', () => { store.set('review', { review: setLabel(store.get().review, target, value) }); announceTally(); });
      label.append(input, ' ', text); set.append(label);
    }
    const clear = document.createElement('button'); clear.type = 'button'; clear.className = 'review-clear'; clear.textContent = 'Clear';
    clear.dataset.focusId = `${group}-clear`;
    clear.setAttribute('aria-label', `Clear mark: ${legend}`);
    clear.addEventListener('click', () => { store.set('review', { review: setLabel(store.get().review, target, null) }); announceTally(); });
    set.append(clear);
    return set;
  }

  function announceTally() {
    const { analysis, review } = store.get();
    const info = analysis?.instruments;
    if (info?.available) announce(reviewTallyText(info.classes, review));
  }

  function renderReview() {
    const { analysis, review, selectedBlock: selectedSegment } = store.get();
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
        play.addEventListener('click', () => { transport.seek(seg.start_s); transport.play(); });
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
    const { analysis, review, selectedBlock: selectedSegment } = store.get();
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
    rovingSegment = index;
    store.set('review', { selectedBlock: index });
    const seg = store.get().analysis?.instruments?.segments?.[index];
    if (seg) announce(`Block ${index + 1}, ${formatTime(seg.start_s)} to ${formatTime(seg.end_s)} selected. Marking controls are below the timeline.`);
  }

  // One real button per block (not per class row), a single tab stop with arrow keys.
  function drawBlockStrip(tl) {
    const { review, selectedBlock: selectedSegment } = store.get();
    const strip = $('blockStrip');
    strip.replaceChildren();
    const count = tl.rows[0].cells.length;
    if (rovingSegment >= count) rovingSegment = 0;
    const active = rovingSegment;
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
    const { analysis } = store.get();
    $('results').hidden = false;
    $('timelineRows').replaceChildren(); $('timelineTable').tBodies[0].replaceChildren();
    $('segmentReview').replaceChildren();
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

  function render(topic) {
    if (!store.get().analysis) return;
    if (topic === 'analysis') showAnalysis();
    else if (topic === 'review') renderReview();
    else if (topic === 'layout') { drawEnergy(); renderReview(); }
  }
  return { render };
}
