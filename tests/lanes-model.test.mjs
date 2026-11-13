import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { lanesFromAnalysis, notAnalysed } from '../web/lanes-model.mjs';

const analysis = JSON.parse(readFileSync(new URL('./fixtures/synthetic-analysis.json', import.meta.url), 'utf8'));

test('lane order is energy then each class in response order', () => {
  assert.deepEqual(lanesFromAnalysis(analysis).map(l => l.id),
    ['energy', 'inst:guitar', 'inst:bass', 'inst:drums', 'inst:voice']);
});

test('provenance and labels distinguish measured from model output', () => {
  const lanes = lanesFromAnalysis(analysis);
  assert.equal(lanes[0].provenance, 'measured');
  assert.equal(lanes[0].label, 'Measured');
  for (const lane of lanes.slice(1)) {
    assert.equal(lane.provenance, 'model');
    assert.equal(lane.label, 'Model output, uncertain');
  }
});

test('energy items are the windows unchanged', () => {
  assert.deepEqual(lanesFromAnalysis(analysis)[0].items, analysis.windows);
});

test('instrument cells stay in track seconds with no passage offset', () => {
  const lanes = lanesFromAnalysis(analysis).slice(1);
  for (const lane of lanes) {
    assert.equal(lane.items.length, 4);
    assert.equal(typeof lane.threshold, 'number');
    for (const c of lane.items) {
      assert.ok(c.start_s >= 120 && c.end_s <= 158.4 + 1e-6);
      for (const k of ['probability', 'above', 'short']) assert.ok(k in c);
    }
  }
  assert.equal(lanes[0].className, 'guitar');
  assert.equal(lanes[0].items[0].start_s, 120);
});

test('unavailable or missing instruments leave only the energy lane', () => {
  for (const instruments of [{ available: false, reason: 'No weights' }, undefined]) {
    const a = { ...analysis, instruments };
    assert.deepEqual(lanesFromAnalysis(a).map(l => l.id), ['energy']);
    assert.deepEqual(notAnalysed(a), []);
  }
});

test('missing or empty windows still give an empty energy lane', () => {
  for (const windows of [undefined, []]) {
    const lanes = lanesFromAnalysis({ ...analysis, windows });
    assert.equal(lanes[0].id, 'energy');
    assert.deepEqual(lanes[0].items, []);
  }
});

test('notAnalysed lists uncovered stretches as start and end', () => {
  const partial = { ...analysis, instruments: { ...analysis.instruments, segments: analysis.instruments.segments.slice(0, 2) } };
  const gaps = notAnalysed(partial);
  assert.equal(gaps.length, 1);
  assert.deepEqual(Object.keys(gaps[0]).sort(), ['end_s', 'start_s']);
  assert.ok(gaps[0].start_s >= 139);
  assert.deepEqual(notAnalysed(analysis), []);
});
