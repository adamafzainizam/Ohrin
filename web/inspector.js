import { measuredObservations, candidateTarget, segmentTarget, setLabel, reviewTallyText, formatTime } from './state.mjs';

const $ = id => document.getElementById(id);
function setText(id, value) { $(id).textContent = value; }
function setTextIfChanged(id, value) { if ($(id).textContent !== value) $(id).textContent = value; }

// Passage findings and listening review. Plot ownership lives in lanes/*.js.
export function createInspector({ store, announce, transport }) {
  const names = ['measured', 'pulse', 'instruments', 'guitar', 'arrange', 'notes'];
  function renderTabs() {
    const { activeTab } = store.get();
    for (const name of names) {
      const active = name === activeTab;
      $(`tab-${name}`).setAttribute('aria-selected', String(active));
      $(`tab-${name}`).tabIndex = active ? 0 : -1;
      $(`panel-${name}`).hidden = !active;
    }
  }
  function activate(name) { store.set('ui', { activeTab: name }); }
  for (const name of names) {
    const tab = $(`tab-${name}`);
    tab.addEventListener('click', () => activate(name));
    tab.addEventListener('keydown', event => {
      const current = names.indexOf(name);
      const next = { ArrowRight: (current + 1) % names.length,
        ArrowLeft: (current + names.length - 1) % names.length,
        Home: 0, End: names.length - 1 }[event.key];
      if (next === undefined) return;
      event.preventDefault(); activate(names[next]); $(`tab-${names[next]}`).focus();
    });
  }
  function renderAvailability() {
    const hasAnalysis = Boolean(store.get().analysis);
    for (const node of $('results').querySelectorAll('.inspector-empty')) node.hidden = hasAnalysis;
    for (const node of $('results').querySelectorAll('.inspector-content')) node.hidden = !hasAnalysis;
    $('results').hidden = false;
  }
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
    const info = analysis?.instruments;
    const available = Boolean(info && info.available);
    $('reviewTally').hidden = !available;
    $('reviewCaveat').hidden = !available;
    const inst = $('instrumentCandidates');
    if (available) {
      inst.replaceChildren();
      for (const c of info.classes) {
        addCandidate(inst, c.name, `score ${c.probability} · ${c.above_threshold ? 'above' : 'below'} tuned threshold ${c.threshold} · uncertain`);
        inst.lastElementChild.append(reviewFieldset(candidateTarget(c.name), `review-class-${c.name}`, `Do you hear ${c.name} in this passage?`));
      }
      setTextIfChanged('reviewTally', reviewTallyText(info.classes, review));
    }
    const box = $('segmentReview');
    box.replaceChildren();
    const seg = available && selectedSegment !== null ? info.segments[selectedSegment] : null;
    box.hidden = !available || !info.segments?.length;
    if (!box.hidden) {
      if (!seg) {
        const p = document.createElement('p'); p.className = 'subtle'; p.textContent = 'Select a block below the lanes to mark what you hear in it.'; box.append(p);
      } else {
        const h = document.createElement('h4');
        h.textContent = `Segment ${formatTime(seg.start_s)}–${formatTime(seg.end_s)} (≈${(seg.end_s - seg.start_s).toFixed(1)} s block)`;
        const play = document.createElement('button'); play.type = 'button'; play.className = 'secondary-button'; play.textContent = 'Play this block';
        play.dataset.focusId = 'seg-play';
        play.addEventListener('click', () => { transport.playRange(seg.start_s, seg.end_s); });
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
    renderReview();
  }

  function render(topic) {
    renderTabs();
    if (topic === 'ui') { if (store.get().analysis) renderReview(); return; }
    renderAvailability();
    if (!store.get().analysis) {
      for (const id of ['observations', 'tempoCandidates', 'chordCandidates', 'instrumentCandidates', 'segmentReview']) $(id).replaceChildren();
      $('reviewTally').hidden = true; $('reviewCaveat').hidden = true; $('segmentReview').hidden = true;
      return;
    }
    if (topic === 'analysis') showAnalysis();
    else if (topic === 'review') renderReview();
  }
  render('analysis');
  return { render };
}
