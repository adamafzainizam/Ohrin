import test from 'node:test';
import assert from 'node:assert/strict';
import { MIN_VIEW_S, timeToX, xToTime, fit, zoomAt, pan, followPage, formatTenths } from '../web/timeline.mjs';

const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);

test('round trip between time and x', () => {
  const view = { start_s: 120, end_s: 158.4 };
  for (const t of [120, 133.3, 158.4]) near(xToTime(timeToX(t, view, 800), view, 800), t);
});

test('passage start maps to the left edge', () => {
  const view = { start_s: 120, end_s: 158.4 };
  assert.equal(timeToX(120, view, 800), 0);
  assert.equal(timeToX(158.4, view, 800), 800);
});

test('zoom keeps the anchor at the same x fraction', () => {
  const view = { start_s: 0, end_s: 100 };
  const out = zoomAt(view, 25, 2, 100);
  near(out.end_s - out.start_s, 50);
  near(timeToX(25, out, 1) , timeToX(25, view, 1));
  near(timeToX(25, out, 1), 0.25);
});

test('clamps to the track', () => {
  assert.deepEqual(fit(-5, 300, 180), { start_s: 0, end_s: 180 });
  assert.deepEqual(pan({ start_s: 170, end_s: 180 }, 20, 180), { start_s: 170, end_s: 180 });
  assert.deepEqual(pan({ start_s: 0, end_s: 10 }, -5, 180), { start_s: 0, end_s: 10 });
  assert.deepEqual(pan({ start_s: 10, end_s: 20 }, 5, 180), { start_s: 15, end_s: 25 });
});

test('minimum span', () => {
  assert.equal(MIN_VIEW_S, 2);
  const z = zoomAt({ start_s: 0, end_s: 4 }, 2, 10, 180);
  near(z.end_s - z.start_s, 2);
  const f = fit(10, 10.5, 180);
  near(f.end_s - f.start_s, 2);
  assert.ok(f.start_s <= 10 && f.end_s >= 10.5);
  assert.deepEqual(fit(0, 0.2, 180), { start_s: 0, end_s: 2 });
  assert.deepEqual(fit(179.9, 180, 180), { start_s: 178, end_s: 180 });
});

test('short track', () => {
  assert.deepEqual(fit(0, 1.5, 1.5), { start_s: 0, end_s: 1.5 });
  assert.deepEqual(zoomAt({ start_s: 0, end_s: 1.5 }, 0.5, 4, 1.5), { start_s: 0, end_s: 1.5 });
});

test('invalid input throws RangeError', () => {
  const v = { start_s: 0, end_s: 10 };
  assert.throws(() => timeToX(1, v, 0), RangeError);
  assert.throws(() => xToTime(1, v, -3), RangeError);
  assert.throws(() => fit(NaN, 1, 10), RangeError);
  assert.throws(() => fit(0, 1, 0), RangeError);
  assert.throws(() => zoomAt(v, 5, Infinity, 100), RangeError);
  assert.throws(() => zoomAt(v, 5, 0, 100), RangeError);
  assert.throws(() => pan(v, NaN, 100), RangeError);
  assert.throws(() => followPage(v, 1, -1), RangeError);
  assert.throws(() => timeToX(1, { start_s: 5, end_s: 5 }, 100), RangeError);
  assert.throws(() => formatTenths(NaN), RangeError);
});

test('formatTenths', () => {
  assert.equal(formatTenths(41.24), '00:41.2');
  assert.equal(formatTenths(192), '03:12.0');
  assert.equal(formatTenths(0), '00:00.0');
  assert.equal(formatTenths(59.96), '01:00.0');
  assert.equal(formatTenths(119.99), '02:00.0');
});

test('followPage', () => {
  const v = { start_s: 0, end_s: 10 };
  assert.deepEqual(followPage(v, 5, 100), v);
  assert.deepEqual(followPage(v, 12, 100), { start_s: 12, end_s: 22 });
  assert.deepEqual(followPage(v, 95, 100), { start_s: 90, end_s: 100 });
});
