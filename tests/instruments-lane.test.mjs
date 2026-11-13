import test from 'node:test';
import assert from 'node:assert/strict';
import { createInstrumentsLane } from '../web/lanes/instruments.js';

// A new-file reset reaches the ui topic before the analysis topic clears the
// old strip. A visible old lane must tolerate that intermediate empty state.
for (const analysis of [null, { instruments: { available: false, reason: 'Synthetic unavailable model' } }]) {
  test(`instrument lane ignores old mark DOM when analysis is ${analysis === null ? 'null' : 'unavailable'}`, t => {
    const previousDocument = globalThis.document;
    t.after(() => { globalThis.document = previousDocument; });
    const oldButton = { dataset: { index: '0' } };
    const strip = { children: [oldButton] };
    globalThis.document = { getElementById: id => id === 'blockStrip' ? strip : null };
    const lane = createInstrumentsLane();
    lane.mount({ hidden: false, querySelectorAll: () => [] }, {});
    assert.doesNotThrow(() => lane.update({ analysis, review: { labels: {} }, selectedBlock: null, analysing: false }));
    assert.equal(strip.children[0], oldButton, 'the later analysis render owns clearing the old strip');
  });
}

function fakeDom() {
  const mk = (extra = {}) => ({ hidden: false, textContent: 'old', children: [1], replaceChildren() { this.children = []; this.textContent = ''; }, ...extra });
  const rows = mk(), strip = mk(), caption = mk({ textContent: 'Old caption' });
  const body = mk(), head = mk();
  const table = { tHead: head, tBodies: [body] };
  const ids = { timelineRows: rows, blockStrip: strip, timelineCaption: caption, timelineTable: table };
  return { ids, rows, strip, caption, body, head, getElementById: id => ids[id] };
}

test('clear() empties and hides the rows, strip, table and caption', t => {
  const previousDocument = globalThis.document;
  t.after(() => { globalThis.document = previousDocument; });
  const dom = fakeDom(); globalThis.document = dom;
  const el = { hidden: false };
  const lane = createInstrumentsLane(); lane.mount(el, {});
  lane.clear();
  assert.equal(el.hidden, true);
  assert.equal(dom.strip.hidden, true);
  assert.equal(dom.caption.hidden, true);
  assert.equal(dom.caption.textContent, '');
  assert.deepEqual(dom.rows.children, []);
  assert.deepEqual(dom.strip.children, []);
  assert.deepEqual(dom.body.children, []);
});

for (const analysis of [null, { instruments: { available: false, reason: 'Synthetic unavailable model' } }]) {
  test(`render with ${analysis === null ? 'no analysis' : 'unavailable instruments'} hides the old caption and strip`, t => {
    const previousDocument = globalThis.document;
    t.after(() => { globalThis.document = previousDocument; });
    const dom = fakeDom(); globalThis.document = dom;
    const el = { hidden: false, querySelectorAll: () => [] };
    const lane = createInstrumentsLane(); lane.mount(el, {});
    lane.render({ state: { analysis, analysing: false, duration: 240, review: { labels: {} }, selectedBlock: null }, view: { start_s: 0, end_s: 240 }, widthPx: 800 });
    assert.equal(el.hidden, true);
    assert.equal(dom.caption.hidden, true);
    assert.equal(dom.caption.textContent, '');
    assert.equal(dom.strip.hidden, true);
    assert.deepEqual(dom.strip.children, []);
  });
}
