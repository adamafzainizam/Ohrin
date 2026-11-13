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

// ---- min/max pyramid ----
import { buildPeakPyramid } from '../web/peaks.mjs';

function lcg(seed) {
  let s = seed >>> 0;
  return () => { s = (Math.imul(s, 1664525) + 1013904223) >>> 0; return s / 2 ** 31 - 1; };
}
function noise(len, seed = 7) {
  const r = lcg(seed), out = new Float32Array(len);
  for (let i = 0; i < len; i++) out[i] = r();
  return out;
}
function rawRange(samples, a, b) {
  a = Math.max(0, a); b = Math.min(samples.length, b);
  let min = Infinity, max = -Infinity;
  for (let k = a; k < b; k++) { if (samples[k] < min) min = samples[k]; if (samples[k] > max) max = samples[k]; }
  return [min, max];
}
function ramp(len) { return Float32Array.from({ length: len }, (_, i) => i); }

test('pyramid of a 10,000-sample ramp stops at once (40 blocks is under 512)', () => {
  const p = buildPeakPyramid(ramp(10000), 256, 4);
  assert.equal(p.baseBlock, 256); assert.equal(p.factor, 4); assert.equal(p.length, 10000);
  assert.equal(p.levels.length, 1);
  const l0 = p.levels[0];
  assert.equal(l0.block, 256); assert.equal(l0.mins.length, 40); assert.equal(l0.maxs.length, 40);
  assert.equal(l0.mins[0], 0); assert.equal(l0.maxs[0], 255);
  assert.equal(l0.mins[39], 9984); assert.equal(l0.maxs[39], 9999); // partial last block
});

test('pyramid levels, block sizes, partial blocks and exact min/max', () => {
  const len = 300_000;
  const p = buildPeakPyramid(ramp(len), 256, 4);
  assert.deepEqual(p.levels.map(l => l.block), [256, 1024]);
  assert.deepEqual(p.levels.map(l => l.mins.length), [1172, 293]);
  for (const l of p.levels) {
    const n = l.mins.length;
    assert.equal(l.mins[0], 0); assert.equal(l.maxs[0], l.block - 1);
    assert.equal(l.mins[n - 1], (n - 1) * l.block); assert.equal(l.maxs[n - 1], len - 1);
  }
  const q = buildPeakPyramid(ramp(10000), 16, 4);
  assert.deepEqual(q.levels.map(l => l.block), [16, 64]);
  assert.deepEqual(q.levels.map(l => l.mins.length), [625, 157]);
  for (const l of q.levels) {
    const n = l.mins.length;
    assert.equal(l.mins[0], 0); assert.equal(l.maxs[0], l.block - 1);
    assert.equal(l.mins[n - 1], (n - 1) * l.block); assert.equal(l.maxs[n - 1], 9999);
  }
});

test('short tracks get one level with one block', () => {
  const p = buildPeakPyramid(Float32Array.from([0.1, -0.5, 0.3]), 256, 4);
  assert.equal(p.levels.length, 1);
  assert.equal(p.levels[0].mins.length, 1);
  assert.equal(p.levels[0].mins[0], Float32Array.of(-0.5)[0]);
  assert.equal(p.levels[0].maxs[0], Float32Array.of(0.3)[0]);
});

test('bin-aligned views give exactly the raw result', () => {
  const sr = 1000, samples = noise(400_000, 3);
  const p = buildPeakPyramid(samples, 256, 4);
  assert.ok(p.levels.length >= 2);
  // 2048 samples per bin: 2 s at 1 kHz is 2000, so use 2.048 s; boundaries at multiples of 1024 and 256.
  const view = { start_s: 1.024, end_s: 1.024 + 2.048 * 100 };
  const raw = peaksForView(samples, sr, view, 100);
  const fast = peaksForView(samples, sr, view, 100, p);
  assert.deepEqual(Array.from(fast), Array.from(raw));
});

test('general views bound the raw result within one block of widening', () => {
  const sr = 8000, samples = noise(2_000_000, 11);
  const p = buildPeakPyramid(samples, 256, 4);
  const r = lcg(99);
  for (let t = 0; t < 60; t++) {
    const start = Math.abs(r()) * 200, len = 0.5 + Math.abs(r()) * 49;
    const bins = 1 + Math.floor(Math.abs(r()) * 400);
    const view = { start_s: start, end_s: start + len };
    const fast = peaksForView(samples, sr, view, bins, p);
    const binDur = len / bins;
    for (let i = 0; i < bins; i++) {
      const a = Math.floor((start + i * binDur) * sr), b = Math.floor((start + (i + 1) * binDur) * sr);
      const spb = b - a;
      const [min, max] = rawRange(samples, a, b);
      assert.ok(fast[2 * i] <= min && fast[2 * i + 1] >= max, `bin ${i} not covering`);
      let block = 0;
      for (const l of p.levels) if (l.block * 2 <= spb) block = l.block;
      if (block === 0) { assert.equal(fast[2 * i], min); assert.equal(fast[2 * i + 1], max); continue; }
      const [wmin, wmax] = rawRange(samples, a - block, b + block);
      assert.ok(fast[2 * i] >= wmin && fast[2 * i + 1] <= wmax, `bin ${i} too wide`);
    }
  }
});

test('pyramid makes a whole-track view on a 10 minute proxy read a tiny fraction', () => {
  const sr = 48000;
  let reads = 0;
  const target = { length: 28_800_000 };
  const samples = new Proxy(target, {
    get(t, prop) {
      if (prop === 'length') return t.length;
      if (typeof prop === 'string' && /^\d+$/.test(prop)) { reads += 1; return 0.5; }
      throw new Error(`unexpected access: ${String(prop)}`);
    },
  });
  const pyramid = buildPeakPyramid(samples);
  assert.equal(reads, target.length);
  reads = 0;
  const peaks = peaksForView(samples, sr, { start_s: 0, end_s: 600 }, 1000, pyramid);
  assert.equal(reads, 0, `pyramid view read ${reads} samples`);
  assert.equal(peaks.length, 2000);
  assert.equal(peaks[0], 0.5); assert.equal(peaks[1], 0.5);
});

test('narrow views fall back to the raw scan', () => {
  const sr = 44100, samples = noise(500_000, 5);
  const p = buildPeakPyramid(samples, 256, 4);
  const view = { start_s: 2, end_s: 2.5 }; // 22050/300 = 73 samples per bin
  assert.deepEqual(Array.from(peaksForView(samples, sr, view, 300, p)), Array.from(peaksForView(samples, sr, view, 300)));
  const edge = { start_s: 2, end_s: 2 + 2 * 500 / sr }; // under 2 * baseBlock per bin
  assert.deepEqual(Array.from(peaksForView(samples, sr, edge, 2, p)), Array.from(peaksForView(samples, sr, edge, 2)));
});

test('edge rules hold with a pyramid', () => {
  const sr = 1000, samples = new Float32Array(100_000).fill(0.25);
  const p = buildPeakPyramid(samples, 256, 4);
  const out = peaksForView(samples, sr, { start_s: -100, end_s: 200 }, 12, p);
  // bins are 25 s wide: -100..-75, -75..-50, -50..-25, -25..0 outside; 0..100 s in range
  for (let i = 0; i < 4; i++) { assert.equal(out[2 * i], 0); assert.equal(out[2 * i + 1], 0); }
  assert.equal(out[8], 0.25); assert.equal(out[9], 0.25);
  assert.equal(out[2 * 11], 0); assert.equal(out[2 * 11 + 1], 0); // past the end
  const ramped = ramp(100_000), pr = buildPeakPyramid(ramped, 256, 4);
  const deep = peaksForView(ramped, sr, { start_s: 10, end_s: 10.001 }, 4, pr);
  for (let i = 0; i < 4; i++) assert.equal(deep[2 * i], deep[2 * i + 1]);
});
