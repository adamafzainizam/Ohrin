import test from 'node:test';
import assert from 'node:assert/strict';
import { zoomKeyAllowed, zoomFactorFromWheel, pickAudioFile, wheelIntent } from '../web/input-rules.mjs';

test('zoom keys are blocked while typing or inside the EQ panel', () => {
  assert.equal(zoomKeyAllowed({ tagName: 'INPUT' }), false);
  assert.equal(zoomKeyAllowed({ tagName: 'TEXTAREA' }), false);
  assert.equal(zoomKeyAllowed({ tagName: 'SELECT' }), false);
  assert.equal(zoomKeyAllowed({ isContentEditable: true }), false);
  assert.equal(zoomKeyAllowed({ tagName: 'BUTTON', closest: (s) => s === '#eqPanel' }), false);
  assert.equal(zoomKeyAllowed({ tagName: 'DIV', closest: () => null }), true);
  assert.equal(zoomKeyAllowed({ tagName: 'DIV' }), true);
  assert.equal(zoomKeyAllowed({}), true);
});

test('wheel factor zooms in on negative deltaY and normalises units', () => {
  assert.ok(zoomFactorFromWheel({ deltaY: -100, deltaMode: 0 }) > 1);
  assert.ok(zoomFactorFromWheel({ deltaY: 100, deltaMode: 0 }) < 1);
  assert.equal(zoomFactorFromWheel({ deltaY: 3, deltaMode: 1 }), zoomFactorFromWheel({ deltaY: 48, deltaMode: 0 }));
  assert.equal(zoomFactorFromWheel({ deltaY: 1, deltaMode: 2 }), zoomFactorFromWheel({ deltaY: 800, deltaMode: 0 }));
});

test('pickAudioFile takes the first audio file, case-insensitively', () => {
  const second = { type: '', name: 'a.FLAC' };
  assert.equal(pickAudioFile([{ type: 'application/json', name: 'eq.json' }, second]), second);
  assert.equal(pickAudioFile([{ type: 'audio/mpeg', name: 'x' }]).name, 'x');
  assert.equal(pickAudioFile([]), null);
});

test('wheelIntent routes zoom, pan and leaves vertical scroll alone', () => {
  const base = { ctrlKey: false, metaKey: false, shiftKey: false, deltaX: 0, deltaY: 0 };
  assert.equal(wheelIntent({ ...base, ctrlKey: true, deltaY: -10 }), 'zoom');
  assert.equal(wheelIntent({ ...base, metaKey: true, deltaY: 10 }), 'zoom');
  assert.equal(wheelIntent({ ...base, shiftKey: true, deltaY: 40 }), 'pan');
  assert.equal(wheelIntent({ ...base, deltaX: 30, deltaY: 4 }), 'pan');
  assert.equal(wheelIntent({ ...base, deltaX: 3, deltaY: 60 }), null);
  assert.equal(wheelIntent({ ...base, deltaY: 60 }), null);
  assert.equal(wheelIntent(base), null);
});

test('zoomAnchor uses the playhead only while playing inside the view', async () => {
  const { zoomAnchor } = await import('../web/input-rules.mjs');
  const view = { start_s: 10, end_s: 20 };
  assert.equal(zoomAnchor(view, 14, true), 14);
  assert.equal(zoomAnchor(view, 100, true), 15, 'playhead outside the view anchors on the centre');
  assert.equal(zoomAnchor(view, 5, true), 15);
  assert.equal(zoomAnchor(view, 14, false), 15, 'paused anchors on the centre');
  assert.equal(zoomAnchor(view, NaN, true), 15);
});

test('zoom at an out-of-view playhead anchor keeps the view near where it was', async () => {
  const { zoomAnchor } = await import('../web/input-rules.mjs');
  const { zoomAt } = await import('../web/timeline.mjs');
  const view = { start_s: 10, end_s: 20 };
  const next = zoomAt(view, zoomAnchor(view, 100, true), 1.5, 240);
  assert.ok(next.start_s >= 10 && next.end_s <= 20, JSON.stringify(next));
});

test('createCoalescer draws once per frame and can be cancelled', async () => {
  const { createCoalescer } = await import('../web/input-rules.mjs');
  const queue = [], cancelled = []; let nextId = 0;
  let draws = 0;
  const c = createCoalescer(() => { draws += 1; }, { request: cb => { queue.push(cb); return ++nextId; }, cancel: id => cancelled.push(id) });
  c.schedule(); c.schedule(); c.schedule();
  assert.equal(queue.length, 1);
  assert.equal(c.pending, true);
  queue.shift()();
  assert.equal(draws, 1);
  assert.equal(c.pending, false);
  c.schedule(); c.cancel();
  assert.deepEqual(cancelled, [2]);
  assert.equal(c.pending, false);
});

test('createAnnouncer joins messages in one window and re-announces repeats', async () => {
  const { createAnnouncer } = await import('../web/input-rules.mjs');
  const writes = [], timers = [];
  const announce = createAnnouncer({ write: t => writes.push(t), schedule: fn => { timers.push(fn); return timers.length; } });
  announce('Marks were cleared');
  announce('Showing 00:10.0 to 00:20.0');
  assert.equal(timers.length, 1, 'one timer for both calls');
  timers.shift()();
  assert.equal(writes.at(-1), 'Marks were cleared. Showing 00:10.0 to 00:20.0');
  announce('Again');
  assert.equal(writes.at(-1), '', 'cleared first');
  timers.shift()();
  assert.equal(writes.at(-1), 'Again');
});
