import test from 'node:test';
import assert from 'node:assert/strict';
import { clampSelection, shouldLoop, shouldStop, measuredObservations } from '../web/state.mjs';

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
