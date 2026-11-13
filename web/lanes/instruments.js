import { timeToX, fit, pan } from '../timeline.mjs';
import { lanesFromAnalysis, notAnalysed } from '../lanes-model.mjs';
import { instrumentTimeline, segmentTarget, formatTime, uncoveredCaption } from '../state.mjs';

const GLYPHS = { heard: '✓', not_heard: '✕', unsure: '?' };
const MARKS = { heard: 'I hear it', not_heard: "I don't hear it", unsure: 'Unsure' };
const $ = id => document.getElementById(id);

export function createInstrumentsLane() {
  let el, actions, args, roving = 0;
  function position(node, start, end, view, widthPx) {
    const left = timeToX(start, view, widthPx), right = timeToX(end, view, widthPx);
    node.style.left = `${left}px`; node.style.width = `${Math.max(0, right - left)}px`;
  }
  function reposition({ view, widthPx }) {
    args = { ...args, view, widthPx };
    for (const node of el.querySelectorAll('[data-start]')) position(node, +node.dataset.start, +node.dataset.end, view, widthPx);
  }
  function span(track, start, end, className, text = '') {
    const node = document.createElement('div'); node.className = className;
    node.dataset.start = start; node.dataset.end = end; node.textContent = text; track.append(node); return node;
  }
  function setRoving(index) {
    const buttons = [...$('blockStrip').children];
    if (!buttons.length) { roving = 0; return; }
    roving = Math.max(0, Math.min(index, buttons.length - 1));
    buttons.forEach((button, i) => { button.tabIndex = i === roving ? 0 : -1; });
  }
  function empty() {
    $('timelineRows').replaceChildren(); $('blockStrip').replaceChildren();
    const table = $('timelineTable'); table.tHead.replaceChildren(); table.tBodies[0].replaceChildren();
    const caption = $('timelineCaption'); caption.textContent = ''; caption.hidden = true;
    $('blockStrip').hidden = true;
    if (el) el.hidden = true;
  }
  function clear() { roving = 0; empty(); }
  function updateMarks() {
    const { state } = args;
    for (const cell of el.querySelectorAll('[data-class]')) {
      const mark = state.review.labels[segmentTarget(+cell.dataset.index, cell.dataset.class)];
      cell.querySelector('.tl-glyph').textContent = GLYPHS[mark] || '';
      cell.classList.toggle('selected', state.selectedBlock === +cell.dataset.index);
      cell.title = cell.dataset.description + (mark ? ` · your mark: ${MARKS[mark]}` : '');
    }
    for (const button of $('blockStrip').children) {
      const index = +button.dataset.index, seg = state.analysis?.instruments?.segments?.[index];
      if (!seg) continue;
      const marks = state.analysis.instruments.classes.map(c => {
        const mark = state.review.labels[segmentTarget(index, c.name)];
        return mark ? `${c.name}: ${MARKS[mark]}` : '';
      }).filter(Boolean);
      button.classList.toggle('selected', state.selectedBlock === index);
      button.setAttribute('aria-current', String(state.selectedBlock === index));
      button.setAttribute('aria-label', `Block ${index + 1} of ${$('blockStrip').children.length}, ${formatTime(seg.start_s)} to ${formatTime(seg.end_s)}${marks.length ? `. Your marks: ${marks.join(', ')}` : ''}`);
    }
  }
  function render(next) {
    args = next;
    const { state } = next;
    const rows = $('timelineRows'), strip = $('blockStrip'), table = $('timelineTable');
    // Clear everything first, including the caption, so no early return leaves an old analysis on screen.
    empty();
    const descriptors = lanesFromAnalysis(state.analysis).filter(lane => lane.provenance === 'model');
    const tl = instrumentTimeline(state.analysis?.instruments, state.analysis?.passage);
    el.hidden = !state.analysing && !tl;
    if (el.hidden) return;
    const lanes = state.analysing && !descriptors.length ? ['guitar', 'bass', 'drums', 'voice'].map(className => ({ className, items: [] })) : descriptors;
    for (const lane of lanes) {
      const row = document.createElement('div'); row.className = 'lane-row';
      const label = document.createElement('div'); label.className = 'lane-label';
      label.textContent = `${lane.className}${lane.threshold === undefined ? '' : ` · threshold ${Number(lane.threshold).toFixed(2)}`}`;
      const track = document.createElement('div'); track.className = `instrument-track lane-plot${state.analysing ? ' lane-skeleton' : ''}`;
      track.setAttribute('aria-hidden', 'true'); row.append(label, track); rows.append(row);
      if (state.analysing) continue;
      const passage = state.analysis.passage;
      const gaps = [{ start_s: 0, end_s: passage.start_s }, ...notAnalysed(state.analysis), { start_s: passage.end_s, end_s: state.duration }];
      for (const gap of gaps) if (gap.end_s > gap.start_s) span(track, gap.start_s, gap.end_s, 'lane-unanalysed', 'Not analysed');
      lane.items.forEach((item, index) => {
        const cell = span(track, item.start_s, item.end_s, `tl-cell${item.short ? ' short' : ''}${item.above ? ' above' : ' below'}`);
        cell.dataset.index = index; cell.dataset.class = lane.className;
        cell.dataset.description = `${lane.className} ${formatTime(item.start_s)} to ${formatTime(item.end_s)} · score ${item.probability === null ? 'n/a' : item.probability.toFixed(2)} · ${item.above ? 'above' : 'below'} threshold ${lane.threshold} · model output, uncertain`;
        const fill = document.createElement('div'); fill.className = 'tl-fill'; fill.style.height = `${Math.max(0, Math.min(1, item.probability || 0)) * 100}%`;
        const glyph = document.createElement('span'); glyph.className = 'tl-glyph'; cell.append(fill, glyph);
        cell.addEventListener('click', () => actions.selectBlock(index));
      });
    }
    if (tl && !state.analysing) {
      const cells = tl.rows[0].cells;
      roving = Math.max(0, Math.min(roving, cells.length - 1));
      cells.forEach((cell, index) => {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'tl-block';
        button.dataset.index = index; button.dataset.focusId = `seg-${index}`;
        button.tabIndex = index === roving ? 0 : -1; button.setAttribute('aria-controls', 'segmentReview'); button.textContent = index + 1;
        button.addEventListener('click', () => { setRoving(index); actions.selectBlock(index); });
        button.addEventListener('focus', () => {
          const { view, state } = args;
          if (cell.start_s >= view.start_s - 1e-6 && cell.end_s <= view.end_s + 1e-6) return;
          const span = view.end_s - view.start_s;
          const next = cell.end_s - cell.start_s > span
            ? fit(cell.start_s, cell.end_s, state.duration)
            : pan(view, cell.start_s < view.start_s ? cell.start_s - view.start_s : cell.end_s - view.end_s, state.duration);
          // Restored or scripted focus only reveals the block; a user's own focus move is a gesture.
          if (actions.programmaticFocus?.()) actions.reveal(next); else actions.setView(next);
        });
        strip.append(button);
      });
      setRoving(state.selectedBlock ?? roving);
      strip.onkeydown = event => {
        const buttons = [...strip.children], current = buttons.indexOf(document.activeElement);
        if (current < 0) return;
        const index = { ArrowRight: current + 1, ArrowDown: current + 1, ArrowLeft: current - 1, ArrowUp: current - 1, Home: 0, End: buttons.length - 1 }[event.key];
        if (index === undefined) return;
        event.preventDefault(); setRoving(index); buttons[roving].focus();
      };
      const head = table.tHead.insertRow();
      for (const text of ['Time', ...tl.rows.map(row => row.name)]) { const th = document.createElement('th'); th.scope = 'col'; th.textContent = text; head.append(th); }
      cells.forEach((cell, index) => {
        const tr = table.tBodies[0].insertRow(); tr.insertCell().textContent = `${formatTime(cell.start_s)} to ${formatTime(cell.end_s)}`;
        for (const row of tl.rows) { const c = row.cells[index]; tr.insertCell().textContent = `${c.probability === null ? 'n/a' : c.probability.toFixed(2)} (${c.above ? 'above' : 'below'})`; }
      });
      const parts = [`Each block averages about ${tl.resolution_s} s of audio (ten 0.96 s VGGish frames), so changes inside a block are invisible and block edges are not entry or exit times. Scores come from an OpenMIC-trained head evaluated on 10 s clips, not on full mixes.`];
      if (tl.shortCount) parts.push(`${tl.shortCount} short block${tl.shortCount === 1 ? ' is' : 's are'} outside the training condition.`);
      const gaps = uncoveredCaption(tl.uncovered); if (gaps) parts.push(gaps);
      if (cells.length === 1) parts.push('Only one block: no change over time can be shown.');
      $('timelineCaption').textContent = parts.join(' '); updateMarks();
    }
    const showStrip = Boolean(tl) && !state.analysing;
    strip.hidden = !showStrip;
    $('timelineCaption').hidden = !showStrip;
    reposition(next);
  }
  return { mount(target, callbacks) { el = target; actions = callbacks; }, render, reposition, clear,
    update(state) {
      const previous = args?.state?.selectedBlock;
      args = { ...args, state };
      if (state.selectedBlock !== null && state.selectedBlock !== previous) setRoving(state.selectedBlock);
      if (!el.hidden && !state.analysing && state.analysis?.instruments?.available) updateMarks();
    } };
}
