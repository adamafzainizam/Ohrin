import test from 'node:test';
import assert from 'node:assert/strict';
import {
  clampSelection, shouldLoop, shouldStop, measuredObservations,
  timeToFraction, instrumentTimeline, SEGMENT_NOMINAL_S,
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
  assert.deepEqual(short.uncovered, [{ start_s: 4.8, end_s: 5 }]);
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
