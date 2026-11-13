import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore, isStale, fileLoaded, KEY_TOPIC, TOPICS } from '../web/store.mjs';

test('subscribe is per topic', () => {
  const store = createStore();
  let viewCalls = 0;
  const off = store.subscribe('view', () => { viewCalls += 1; });
  store.set({ selectedBlock: 2 });
  assert.equal(viewCalls, 0);
  store.set({ view: { start_s: 1, end_s: 5 } });
  assert.equal(viewCalls, 1);
  off();
  store.set({ view: { start_s: 2, end_s: 6 } });
  assert.equal(viewCalls, 1);
});

test('subscribers get new state in order; unknown subscribe topic throws', () => {
  const store = createStore();
  const seen = [];
  store.subscribe('ui', s => seen.push(['a', s.activeTab]));
  store.subscribe('ui', s => seen.push(['b', s.activeTab]));
  store.set({ activeTab: 'notes' });
  assert.deepEqual(seen, [['a', 'notes'], ['b', 'notes']]);
  assert.equal(store.get().activeTab, 'notes');
    assert.throws(() => store.subscribe('nope', () => {}), TypeError);
});

test('defaults', () => {
  const s = createStore().get();
  assert.equal(s.file, null);
  assert.equal(s.duration, 0);
  assert.deepEqual(s.selection, { start: 0, end: 0 });
  assert.deepEqual(s.view, { start_s: 0, end_s: 0 });
  assert.equal(s.analysis, null);
  assert.equal(s.analysisRequest, null);
  assert.deepEqual(s.review, { key: null, labels: {} });
  assert.equal(s.selectedBlock, null);
  assert.equal(s.activeTab, 'measured');
  assert.equal(s.followPaused, false);
  assert.equal(s.analysing, false);
});

test('analysis passage shorter than request is not stale', () => {
  const store = createStore({
    selection: { start: 10, end: 30 },
    analysisRequest: { start: 10, end: 30 },
    analysis: { passage: { start_s: 10, end_s: 29.2 } },
  });
  assert.equal(isStale(store.get()), false);
});

test('stale after committed change and clean again when restored', () => {
  const store = createStore({
    selection: { start: 10, end: 30 },
    analysisRequest: { start: 10, end: 30 },
    analysis: { passage: { start_s: 10, end_s: 29.2 } },
  });
  store.set({ selection: { start: 12, end: 30 } });
  assert.equal(isStale(store.get()), true);
  store.set({ selection: { start: 10, end: 30 } });
  assert.equal(isStale(store.get()), false);
});

test('no analysis, never stale', () => {
  const store = createStore({ selection: { start: 1, end: 2 }, analysisRequest: { start: 5, end: 6 } });
  assert.equal(isStale(store.get()), false);
  assert.equal(isStale({ analysis: {}, analysisRequest: null, selection: { start: 1, end: 2 } }), false);
});

test('new file clears', () => {
  const store = createStore({
    analysis: { x: 1 }, analysisRequest: { start: 1, end: 2 },
    review: { key: 'k', labels: { a: 'heard' } }, selectedBlock: 3, followPaused: true,
  });
  const before = store.get();
  const file = {};
  const patch = fileLoaded(before, file, 200);
  assert.equal(before.analysis.x, 1);
  assert.equal(patch.file, file);
  assert.equal(patch.duration, 200);
  assert.equal(patch.analysis, null);
  assert.equal(patch.analysisRequest, null);
  assert.deepEqual(patch.review, { key: null, labels: {} });
  assert.equal(patch.selectedBlock, null);
  assert.equal(patch.followPaused, false);
  assert.deepEqual(patch.view, { start_s: 0, end_s: 200 });
});

test('zoom does not touch selection', () => {
  const store = createStore({ selection: { start: 3, end: 9 } });
  const sel = store.get().selection;
  store.set({ view: { start_s: 0, end_s: 50 } });
  assert.deepEqual(store.get().selection, { start: 3, end: 9 });
  assert.equal(store.get().selection, sel);
});

test('a throwing subscriber does not skip the others and is reported', t => {
  const errors = [];
  t.mock.method(console, 'error', (...args) => { errors.push(args); });
  const store = createStore();
  const ran = [];
  store.subscribe('ui', () => { throw new Error('boom'); });
  store.subscribe('ui', () => ran.push('second'));
  assert.doesNotThrow(() => store.set({ activeTab: 'notes' }));
  assert.deepEqual(ran, ['second']);
  assert.equal(errors.length, 1);
  assert.equal(store.get().activeTab, 'notes');
});

const SAMPLE = {
  selection: { start: 1, end: 2 }, view: { start_s: 1, end_s: 2 }, analysis: { x: 1 },
  analysisRequest: { start: 1, end: 2 }, review: { key: 'k', labels: {} }, file: {}, duration: 9,
  selectedBlock: 4, activeTab: 'notes', followPaused: true, analysing: true,
};

test('KEY_TOPIC is frozen and maps every key to its topic', () => {
  assert.ok(Object.isFrozen(KEY_TOPIC));
  assert.deepEqual({ ...KEY_TOPIC }, {
    selection: 'selection', view: 'view', analysis: 'analysis', analysisRequest: 'analysis', review: 'review',
    file: 'ui', duration: 'ui', selectedBlock: 'ui', activeTab: 'ui', followPaused: 'ui', analysing: 'ui',
  });
  assert.deepEqual(Object.keys(SAMPLE).sort(), Object.keys(KEY_TOPIC).sort());
});

test('each key notifies exactly its topic', () => {
  for (const [key, topic] of Object.entries(KEY_TOPIC)) {
    const store = createStore();
    const hits = [];
    for (const t of TOPICS) store.subscribe(t, () => hits.push(t));
    store.set({ [key]: SAMPLE[key] });
    assert.deepEqual(hits, [topic], key);
  }
});

test('a patch spanning three topics notifies each once, in TOPICS order', () => {
  const store = createStore();
  const hits = [];
  for (const t of TOPICS) store.subscribe(t, () => hits.push(t));
  store.set({ activeTab: 'notes', analysing: true, view: { start_s: 1, end_s: 2 }, analysis: { x: 1 }, analysisRequest: { start: 0, end: 1 } });
  assert.deepEqual(hits, ['view', 'analysis', 'ui']);
});

test('every subscriber sees the fully applied state', () => {
  const store = createStore();
  const seen = [];
  for (const t of TOPICS) store.subscribe(t, s => seen.push([t, s.selection.end, s.view.end_s, s.analysis && s.analysis.x, s.review.key, s.activeTab]));
  store.set({ activeTab: 'notes', review: { key: 'k', labels: {} }, analysis: { x: 1 }, view: { start_s: 0, end_s: 7 }, selection: { start: 0, end: 5 } });
  assert.deepEqual(seen.map(r => r[0]), ['selection', 'view', 'analysis', 'review', 'ui']);
  for (const row of seen) assert.deepEqual(row.slice(1), [5, 7, 1, 'k', 'notes']);
});

test('an unknown key throws and leaves state unchanged and unnotified', () => {
  const store = createStore();
  const before = store.get();
  let calls = 0;
  for (const t of TOPICS) store.subscribe(t, () => { calls += 1; });
  assert.throws(() => store.set({ activeTab: 'notes', nope: 1 }), TypeError);
  assert.equal(store.get(), before);
  assert.equal(store.get().activeTab, 'measured');
  assert.equal(calls, 0);
});

test('a non-object patch throws', () => {
  const store = createStore();
  for (const bad of ['ui', null, undefined, 5, true, []]) assert.throws(() => store.set(bad), TypeError);
  assert.throws(() => store.set('ui', { activeTab: 'notes' }), TypeError);
});

test('unchanged values notify nobody; only the changed key topic is notified', () => {
  const store = createStore();
  const hits = [];
  for (const t of TOPICS) store.subscribe(t, () => hits.push(t));
  store.set({ activeTab: 'measured', analysing: false, analysis: null });
  store.set({});
  assert.deepEqual(hits, []);
  store.set({ activeTab: 'measured', analysing: true });
  assert.deepEqual(hits, ['ui']);
  hits.length = 0;
  store.set({ followPaused: false, view: { start_s: 3, end_s: 4 } });
  assert.deepEqual(hits, ['view']);
});
