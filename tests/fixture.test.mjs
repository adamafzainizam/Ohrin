import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { instrumentTimeline, measuredObservations } from '../web/state.mjs';

const fixture = JSON.parse(readFileSync(new URL('./fixtures/synthetic-analysis.json', import.meta.url), 'utf8'));

test('synthetic fixture parses and the passage starts at 120 s', () => {
  assert.equal(fixture.passage.start_s, 120);
  assert.match(fixture._comment, /Synthetic/);
});

test('synthetic fixture windows and segments lie inside the passage', () => {
  const { start_s, end_s } = fixture.passage;
  for (const w of fixture.windows) assert.ok(w.start_s >= start_s && w.end_s <= end_s, `window ${w.start_s}`);
  for (const s of fixture.instruments.segments) assert.ok(s.start_s >= start_s && s.end_s <= end_s, `segment ${s.start_s}`);
});

test('synthetic fixture drives the timeline and observations', () => {
  const tl = instrumentTimeline(fixture.instruments, fixture.passage);
  assert.equal(tl.rows.length, 4);
  for (const row of tl.rows) assert.equal(row.cells.length, 4);
  assert.ok(Array.isArray(measuredObservations(fixture.windows)));
});

test('synthetic fixture chords carry what showAnalysis reads', () => {
  for (const c of fixture.candidate_chords) {
    assert.ok(Array.isArray(c.suggested_standard_tuning_frets));
    assert.equal(typeof c.score, 'number');
  }
});
