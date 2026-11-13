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
