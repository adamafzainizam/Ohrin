import test from 'node:test';
import assert from 'node:assert/strict';
import { createStore, isStale, fileLoaded } from '../web/store.mjs';

test('subscribe is per topic', () => {
  const store = createStore();
  let viewCalls = 0;
  const off = store.subscribe('view', () => { viewCalls += 1; });
  store.set('review', { selectedBlock: null });
  assert.equal(viewCalls, 0);
  store.set('view', { view: { start_s: 1, end_s: 5 } });
  assert.equal(viewCalls, 1);
  off();
  store.set('view', { view: { start_s: 2, end_s: 6 } });
  assert.equal(viewCalls, 1);
});

test('subscribers get new state in order; unknown topic throws', () => {
  const store = createStore();
  const seen = [];
  store.subscribe('ui', s => seen.push(['a', s.activeTab]));
  store.subscribe('ui', s => seen.push(['b', s.activeTab]));
  store.set('ui', { activeTab: 'notes' });
  assert.deepEqual(seen, [['a', 'notes'], ['b', 'notes']]);
  assert.equal(store.get().activeTab, 'notes');
  assert.throws(() => store.set('nope', {}), TypeError);
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
  store.set('selection', { selection: { start: 12, end: 30 } });
  assert.equal(isStale(store.get()), true);
  store.set('selection', { selection: { start: 10, end: 30 } });
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
  store.set('view', { view: { start_s: 0, end_s: 50 } });
  assert.deepEqual(store.get().selection, { start: 3, end: 9 });
  assert.equal(store.get().selection, sel);
});
