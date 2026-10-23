import './eq.test.mjs';
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  clampSelection, shouldLoop, shouldStop, measuredObservations,
  timeToFraction, instrumentTimeline, SEGMENT_NOMINAL_S,
  LABELS, emptyReview, reviewKey, candidateTarget, segmentTarget, setLabel, toggleLabel,
  reconcileReview, pruneReview, reviewSummary, isStaleResponse, settleRequest,
  dragExceeded, DRAG_THRESHOLD_PX, isPrimaryPress, primaryButtonHeld, resolveDrag, uncoveredCaption, reviewTallyText,
  NOTE_SCHEMA, NOTE_SCHEMA_VERSION, buildPassageNote,
} from '../web/state.mjs';

test('selection stays within the decoded duration and keeps order', () => {
  assert.deepEqual(clampSelection(9, 3, 8), { start: 3, end: 8 });
  assert.deepEqual(clampSelection(-4, 2, 8), { start: 0, end: 2 });
});

test('loop fires at the selected end only while enabled', () => {
  assert.equal(shouldLoop(8.1, { start: 3, end: 8 }, true), true);
  assert.equal(shouldLoop(7.9, { start: 3, end: 8 }, true), false);
  assert.equal(shouldLoop(8.1, { start: 3, end: 8 }, false), false);
});

test('playback stops at passage end when loop is off', () => {
  assert.equal(shouldStop(8.1, { start: 3, end: 8 }, false), true);
  assert.equal(shouldStop(7.9, { start: 3, end: 8 }, false), false);
  assert.equal(shouldStop(8.1, { start: 3, end: 8 }, true), false);
});

test('observations name the measured level drop', () => {
  const observations = measuredObservations([
    { start_s: 2, end_s: 3, rms_dbfs: -8, band_fraction: [0.5, 0.4, 0.1] },
    { start_s: 3, end_s: 4, rms_dbfs: -19, band_fraction: [0.1, 0.85, 0.05] },
  ]);
  assert.match(observations[0], /00:03.*11\.0 dB/);
});

const PASSAGE = { start_s: 10, end_s: 40 };

test('timeToFraction clamps and handles degenerate passages', () => {
  assert.equal(timeToFraction(10, PASSAGE), 0);
  assert.equal(timeToFraction(25, PASSAGE), 0.5);
  assert.equal(timeToFraction(250, PASSAGE), 1);
  assert.equal(timeToFraction(4, PASSAGE), 0);
  assert.equal(timeToFraction(5, { start_s: 3, end_s: 3 }), 0);
  assert.equal(timeToFraction(NaN, PASSAGE), 0);
});

test('instrumentTimeline returns null when unavailable or empty', () => {
  assert.equal(instrumentTimeline({ available: false }, PASSAGE), null);
  assert.equal(instrumentTimeline(undefined, PASSAGE), null);
  assert.equal(instrumentTimeline({ available: true, segments: [] }, PASSAGE), null);
});

test('instrumentTimeline marks threshold crossings per class', () => {
  const t = instrumentTimeline({
    available: true,
    classes: [{ name: 'bass', threshold: 0.3 }, { name: 'guitar', threshold: 0.40 }],
    segments: [
      { start_s: 0, end_s: 9.6, probabilities: { guitar: 0.40, bass: 0.29 } },
      { start_s: 9.6, end_s: 19.2, probabilities: { guitar: 0.2, bass: 0.3 } },
    ],
  }, { start_s: 0, end_s: 19.2 });
  assert.deepEqual(t.rows.map(r => r.name), ['bass', 'guitar']);
  assert.equal(t.resolution_s, SEGMENT_NOMINAL_S);
  assert.deepEqual(t.rows[0].cells.map(c => c.above), [false, true]);
  assert.deepEqual(t.rows[1].cells.map(c => c.above), [true, false]);
  assert.equal(t.rows[1].threshold, 0.40);
});

test('instrumentTimeline reports dropped tail and short blocks', () => {
  const classes = [{ name: 'guitar', threshold: 0.5 }];
  const full = instrumentTimeline({ available: true, classes, segments: [
    { start_s: 0, end_s: 9.6, probabilities: { guitar: 0.1 } },
    { start_s: 9.6, end_s: 19.2, probabilities: { guitar: 0.1 } },
  ] }, { start_s: 0, end_s: 21 });
  assert.equal(full.uncovered.length, 1);
  assert.ok(Math.abs(full.uncovered[0].start_s - 19.2) < 1e-9);
  assert.equal(full.uncovered[0].end_s, 21);
  assert.equal(full.shortCount, 0);
  const short = instrumentTimeline({ available: true, classes, segments: [
    { start_s: 0, end_s: 4.8, probabilities: { guitar: 0.1 } },
  ] }, { start_s: 0, end_s: 5 });
  assert.equal(short.shortCount, 1);
  assert.equal(short.rows[0].cells[0].short, true);
  assert.deepEqual(short.uncovered, [{ start_s: 4.8, end_s: 5, position: 'end' }]);
});

test('instrumentTimeline geometry sums to covered fraction', () => {
  const classes = [{ name: 'guitar', threshold: 0.5 }];
  const t = instrumentTimeline({ available: true, classes, segments: [
    { start_s: 0, end_s: 9.6, probabilities: { guitar: 0.1 } },
    { start_s: 9.6, end_s: 19.2, probabilities: { guitar: 0.9 } },
  ] }, { start_s: 0, end_s: 21 });
  const [a, b] = t.rows[0].cells;
  assert.ok(Math.abs(a.left + a.width - b.left) < 1e-9);
  assert.ok(Math.abs(b.left + b.width - 19.2 / 21) < 1e-9);
});

test('instrumentTimeline tolerates missing class probability', () => {
  const t = instrumentTimeline({ available: true, classes: [{ name: 'voice', threshold: 0.4 }],
    segments: [{ start_s: 0, end_s: 9.6, probabilities: {} }] }, { start_s: 0, end_s: 9.6 });
  assert.equal(t.rows[0].cells[0].probability, null);
  assert.equal(t.rows[0].cells[0].above, false);
});

const INSTRUMENTS = {
  available: true, model: 'fake', caveat: 'c',
  classes: [
    { name: 'guitar', probability: 0.71, threshold: 0.40, above_threshold: true },
    { name: 'bass', probability: 0.2, threshold: 0.3, above_threshold: false },
  ],
  segments: [
    { start_s: 10, end_s: 19.6, probabilities: { guitar: 0.8, bass: 0.58 } },
    { start_s: 19.6, end_s: 29.2, probabilities: { guitar: 0.7, bass: 0.1 } },
  ],
};
const ANALYSIS = {
  passage: { start_s: 10, end_s: 29.2 },
  windows: [{ start_s: 10, end_s: 11, rms_dbfs: -10, band_fraction: [0.5, 0.3, 0.2] },
            { start_s: 11, end_s: 12, rms_dbfs: -20, band_fraction: [0.5, 0.3, 0.2] }],
  tempo_candidates: [{ bpm: 100, periodicity: 0.5 }],
  candidate_chords: [{ name: 'Em' }],
  instruments: INSTRUMENTS,
  limitations: ['Instrument candidates are uncertain model outputs, not confirmed parts.'],
};
const NOTE_KEYS = ['schema', 'schema_version', 'app', 'exported_at', 'file_name', 'passage', 'measured_observations',
  'tempo_candidates', 'harmonic_candidates', 'chord_correction', 'arrangement_notes', 'instrument_candidates',
  'listening_review', 'limitations', 'eq'].sort();

test('setLabel sets, replaces and clears without mutating input', () => {
  const r0 = emptyReview('k');
  const r1 = setLabel(r0, candidateTarget('guitar'), 'heard');
  assert.deepEqual(r0.labels, {});
  assert.equal(r1.labels['class:guitar'], 'heard');
  const r2 = setLabel(r1, 'class:guitar', 'unsure');
  assert.equal(r1.labels['class:guitar'], 'heard');
  assert.equal(r2.labels['class:guitar'], 'unsure');
  const r3 = setLabel(r2, 'class:guitar', null);
  assert.deepEqual(r3.labels, {});
  assert.equal(r3.key, 'k');
  assert.deepEqual([...LABELS], ['heard', 'not_heard', 'unsure']);
});

test('setLabel rejects unknown labels and malformed targets', () => {
  const r = emptyReview('k');
  assert.throws(() => setLabel(r, 'class:guitar', 'yes'), TypeError);
  assert.throws(() => setLabel(r, 'class:', 'heard'), TypeError);
  assert.throws(() => setLabel(r, 'seg:x:guitar', 'heard'), TypeError);
  assert.throws(() => setLabel(r, 'class:Guitar<script>', 'heard'), TypeError);
  assert.equal(segmentTarget(2, 'bass'), 'seg:2:bass');
});

test('toggleLabel clears when the same label is chosen again', () => {
  const r1 = toggleLabel(emptyReview('k'), 'class:bass', 'unsure');
  assert.equal(r1.labels['class:bass'], 'unsure');
  const r2 = toggleLabel(r1, 'class:bass', 'unsure');
  assert.deepEqual(r2.labels, {});
  assert.equal(toggleLabel(r1, 'class:bass', 'heard').labels['class:bass'], 'heard');
});

test('reviewKey is null when file metadata or passage is missing', () => {
  const f = { name: 'a.wav', size: 10, lastModified: 5 };
  assert.equal(reviewKey(f, { start_s: 1, end_s: 2 }), 'a.wav|10|5|1|2');
  assert.equal(reviewKey(null, { start_s: 1, end_s: 2 }), null);
  assert.equal(reviewKey({ name: 'a.wav', size: 10 }, { start_s: 1, end_s: 2 }), null);
  assert.equal(reviewKey(f, null), null);
  assert.equal(reviewKey(f, { start_s: 1 }), null);
});

test('reconcileReview keeps labels only for the identical file and passage', () => {
  const r = setLabel(emptyReview('a'), 'class:guitar', 'heard');
  assert.equal(reconcileReview(r, 'a'), r);
  assert.deepEqual(reconcileReview(r, 'b'), { key: 'b', labels: {} });
  assert.deepEqual(reconcileReview(r, null), { key: null, labels: {} });
});

test('pruneReview drops labels for classes or segments not in the new output', () => {
  let r = emptyReview('k');
  for (const [t, l] of [['class:guitar', 'heard'], ['class:voice', 'heard'], ['seg:1:bass', 'unsure'], ['seg:5:bass', 'unsure'], ['seg:0:voice', 'heard']]) r = setLabel(r, t, l);
  const pruned = pruneReview(r, INSTRUMENTS);
  assert.deepEqual(Object.keys(pruned.labels).sort(), ['class:guitar', 'seg:1:bass']);
  assert.deepEqual(pruneReview(r, { available: false }).labels, {});
});

test('reviewSummary counts by label', () => {
  let r = emptyReview('k');
  r = setLabel(r, 'class:guitar', 'heard'); r = setLabel(r, 'class:bass', 'unsure'); r = setLabel(r, 'seg:0:bass', 'unsure');
  assert.deepEqual(reviewSummary(r), { heard: 1, not_heard: 0, unsure: 2, total: 3 });
});

test('isStaleResponse detects file or selection change during analysis', () => {
  const f = {};
  const req = { file: f, selection: { start: 1, end: 5 } };
  assert.equal(isStaleResponse(req, { file: f, selection: { start: 1, end: 5 } }), false);
  assert.equal(isStaleResponse(req, { file: {}, selection: { start: 1, end: 5 } }), true);
  assert.equal(isStaleResponse(req, { file: f, selection: { start: 1, end: 5.1 } }), true);
  assert.equal(isStaleResponse(req, { file: null, selection: { start: 1, end: 5 } }), true);
});

test('buildPassageNote v3 includes schema version and listening review', () => {
  let r = emptyReview('k');
  r = setLabel(r, 'class:guitar', 'heard'); r = setLabel(r, 'seg:0:bass', 'unsure');
  const note = buildPassageNote({ fileName: 'x.wav', analysis: ANALYSIS, chordCorrection: 'Em7', arrangementNotes: 'n', review: r, exportedAt: '2026-10-14T00:00:00.000Z' });
  assert.equal(note.schema, NOTE_SCHEMA);
  assert.equal(note.schema, 'ohrin.passage-note');
  assert.equal(note.schema_version, 3);
  assert.equal(NOTE_SCHEMA_VERSION, 3);
  assert.equal(note.exported_at, '2026-10-14T00:00:00.000Z');
  const lr = note.listening_review;
  assert.deepEqual(lr.candidates.map(c => c.instrument), ['guitar', 'bass']);
  assert.equal(lr.candidates[0].your_label, 'heard');
  assert.equal(lr.candidates[0].model_above_threshold, true);
  assert.equal(lr.candidates[1].your_label, null);
  assert.equal(lr.segments.length, 1);
  assert.deepEqual(lr.segments[0], { index: 0, start_s: 10, end_s: 19.6, instrument: 'bass', model_score: 0.58, model_above_threshold: true, your_label: 'unsure' });
  assert.deepEqual(lr.summary, { heard: 1, not_heard: 0, unsure: 1, total: 2 });
  assert.ok(note.limitations.includes('Listening marks are user judgements, not validation of the model.'));
});

test('buildPassageNote never includes audio or raw windows', () => {
  const note = buildPassageNote({ fileName: 'x.wav', analysis: { ...ANALYSIS, audio: 'x', blob: 'blob:1', data: 'data:audio/wav' },
    chordCorrection: '', arrangementNotes: '', review: emptyReview('k'), exportedAt: 't' });
  assert.deepEqual(Object.keys(note).sort(), NOTE_KEYS);
  const text = JSON.stringify(note);
  for (const bad of ['windows', 'rms_dbfs', 'data:audio', 'blob:']) assert.equal(text.includes(bad), false, bad);
  assert.equal(note.limitations.includes('Listening marks are user judgements, not validation of the model.'), false);
});

test('buildPassageNote with unavailable instruments sets listening_review null', () => {
  const note = buildPassageNote({ fileName: 'x.wav', analysis: { ...ANALYSIS, instruments: { available: false, reason: 'r' } },
    chordCorrection: '', arrangementNotes: '', review: emptyReview('k'), exportedAt: 't' });
  assert.equal(note.listening_review, null);
  assert.deepEqual(note.instrument_candidates, { available: false, reason: 'r' });
});

test('settleRequest: a stale failed request touches neither status nor the button while a newer one runs', () => {
  const f = {};
  const sel = { start: 1, end: 5 };
  const old = { id: 1, file: f, selection: sel };
  const current = { id: 2, file: f, selection: sel };
  assert.deepEqual(settleRequest(old, current), { stale: true, ownsButton: false });
  assert.deepEqual(settleRequest({ id: 2, file: f, selection: sel }, current), { stale: false, ownsButton: true });
});

test('settleRequest: the latest request still owns the button when the selection changed', () => {
  const f = {};
  const r = { id: 3, file: f, selection: { start: 1, end: 5 } };
  assert.deepEqual(settleRequest(r, { id: 3, file: f, selection: { start: 2, end: 5 } }), { stale: true, ownsButton: true });
  assert.deepEqual(settleRequest(r, { id: 3, file: {}, selection: { start: 1, end: 5 } }), { stale: true, ownsButton: true });
});

test('dragExceeded ignores sub-threshold pointer wobble', () => {
  assert.equal(dragExceeded(100, 101), false);
  assert.equal(dragExceeded(100, 100 + DRAG_THRESHOLD_PX - 1), false);
  assert.equal(dragExceeded(100, 100 + DRAG_THRESHOLD_PX), true);
  assert.equal(dragExceeded(100, 90), true);
  assert.equal(dragExceeded(NaN, 90), false);
});

test('instrumentTimeline returns null when available but classes are empty or missing', () => {
  const segments = [{ start_s: 0, end_s: 9.6, probabilities: {} }];
  assert.equal(instrumentTimeline({ available: true, classes: [], segments }, { start_s: 0, end_s: 9.6 }), null);
  assert.equal(instrumentTimeline({ available: true, segments }, { start_s: 0, end_s: 9.6 }), null);
});

test('setLabel accepts any class name from the analysis, such as mallet_percussion', () => {
  const r = setLabel(emptyReview('k'), candidateTarget('mallet_percussion'), 'heard');
  assert.equal(r.labels['class:mallet_percussion'], 'heard');
  const r2 = setLabel(r, segmentTarget(3, 'mallet_percussion'), 'unsure');
  assert.equal(r2.labels['seg:3:mallet_percussion'], 'unsure');
  assert.throws(() => setLabel(r, 'class:Mallet', 'heard'), TypeError);
});

test('uncovered captions say final, first or middle according to where the gap is', () => {
  const classes = [{ name: 'bass', threshold: 0.3 }];
  const mk = segments => instrumentTimeline({ available: true, classes, segments: segments.map(([a, b]) => ({ start_s: a, end_s: b, probabilities: { bass: 0.1 } })) }, { start_s: 0, end_s: 30 });
  assert.match(uncoveredCaption(mk([[0, 9.6], [9.6, 19.2]]).uncovered), /^The final 10\.8 s was not scored\.$/);
  const head = uncoveredCaption(mk([[5, 30]]).uncovered);
  assert.match(head, /^The first 5\.0 s/);
  assert.equal(head.includes('final'), false);
  const mid = uncoveredCaption(mk([[0, 9.6], [20, 30]]).uncovered);
  assert.match(mid, /10\.4 s between 00:09 and 00:20/);
  assert.equal(mid.includes('final'), false);
  assert.equal(uncoveredCaption([]), '');
});

test('reviewTallyText counts candidate marks and block marks', () => {
  const classes = INSTRUMENTS.classes;
  let r = emptyReview('k');
  assert.equal(reviewTallyText(classes, r), 'You marked 0 of 2 candidates.');
  r = setLabel(r, 'class:guitar', 'heard'); r = setLabel(r, 'seg:0:bass', 'unsure');
  assert.equal(reviewTallyText(classes, r), 'You marked 1 of 2 candidates (1 heard). 1 block mark set.');
});

test('only the primary button of the primary pointer starts a drag', () => {
  assert.equal(isPrimaryPress({ button: 0, isPrimary: true }), true);
  assert.equal(isPrimaryPress({ button: 2, isPrimary: true }), false);
  assert.equal(isPrimaryPress({ button: 0, isPrimary: false }), false);
});

test('a move with no primary button held ends the drag', () => {
  assert.equal(primaryButtonHeld({ buttons: 1 }), true);
  assert.equal(primaryButtonHeld({ buttons: 0 }), false);
  assert.equal(primaryButtonHeld({ buttons: 2 }), false);
});

test('a selection commits only past the pixel threshold and the minimum duration', () => {
  assert.equal(resolveDrag({ startX: 100, endX: 160, startS: 5, endS: 9 }), 'commit');
  assert.equal(resolveDrag({ startX: 100, endX: 101, startS: 5, endS: 9 }), 'click');
  assert.equal(resolveDrag({ startX: 100, endX: 160, startS: 5, endS: 5.1 }), 'click');
  assert.equal(resolveDrag({ startX: 100, endX: 40, startS: 9, endS: 5 }), 'commit');
});

test('EQ note exports settings only and adds limitation only for nonflat bands', async () => {
  const {defaultBands}=await import('../web/eq.mjs');
  const args={fileName:'synthetic.wav',analysis:ANALYSIS,chordCorrection:'',arrangementNotes:'',review:emptyReview(),exportedAt:'test'};
  const flat=buildPassageNote(args);const bands=defaultBands();bands[1].gain_db=6;
  const shaped=buildPassageNote({...args,eq:{bands,samples:[1],spectrum:[2],audio:'bad',solo:1}});
  assert.equal(shaped.limitations.length,flat.limitations.length+1);
  const walk=o=>{for(const [key,value] of Object.entries(o||{})){assert.ok(!['audio','samples','spectrum','solo'].includes(key));if(value&&typeof value==='object')walk(value);}};
  walk(shaped);assert.equal(shaped.eq.bands[1].gain_db,6);
});
