import test from 'node:test';
import assert from 'node:assert/strict';
import { peaksForView } from '../web/peaks.mjs';

test('peaks length and sine amplitude', () => {
  const sr = 48000;
  const samples = new Float32Array(sr);
  for (let i = 0; i < sr; i++) samples[i] = Math.sin(2 * Math.PI * 440 * i / sr);
  const peaks = peaksForView(samples, sr, { start_s: 0, end_s: 1 }, 100);
  assert.equal(peaks.length, 200);
  for (let i = 0; i < 100; i++) {
    assert.ok(peaks[2 * i + 1] <= 1 && peaks[2 * i + 1] >= 0.9, `max ${i}: ${peaks[2 * i + 1]}`);
    assert.ok(peaks[2 * i] >= -1 && peaks[2 * i] <= -0.9, `min ${i}: ${peaks[2 * i]}`);
  }
});

test('60 minute track is read through indexing only, within a bounded read budget', () => {
  const sr = 48000;
  let reads = 0;
  const samples = new Proxy({ length: 172_800_000 }, {
    get(target, prop) {
      if (prop === 'length') return target.length;
      if (typeof prop === 'string' && /^\d+$/.test(prop)) { reads += 1; return 0.5; }
      throw new Error(`unexpected access: ${String(prop)}`);
    },
  });
  const t0 = performance.now();
  const peaks = peaksForView(samples, sr, { start_s: 1800, end_s: 1802 }, 800);
  const ms = performance.now() - t0;
  assert.equal(peaks.length, 1600);
  assert.ok(reads <= 2 * 48000 + 800, `reads ${reads}`);
  assert.ok(ms < 2000, `took ${ms} ms`);
  assert.equal(peaks[0], 0.5);
  assert.equal(peaks[1], 0.5);
});

test('bins outside the track are zeros', () => {
  const sr = 1000;
  const samples = new Float32Array(sr).fill(0.25);
  const peaks = peaksForView(samples, sr, { start_s: -1, end_s: 1 }, 10);
  for (let i = 0; i < 5; i++) { assert.equal(peaks[2 * i], 0); assert.equal(peaks[2 * i + 1], 0); }
  for (let i = 5; i < 10; i++) { assert.equal(peaks[2 * i], 0.25); assert.equal(peaks[2 * i + 1], 0.25); }
});

test('deep zoom reads the floor sample so there are no holes', () => {
  const sr = 100;
  const samples = Float32Array.from({ length: 100 }, (_, i) => i / 100);
  const peaks = peaksForView(samples, sr, { start_s: 0.10, end_s: 0.11 }, 10);
  for (let i = 0; i < 10; i++) {
    assert.equal(peaks[2 * i], peaks[2 * i + 1]);
    assert.ok(peaks[2 * i] > 0, `hole at ${i}`);
  }
});
