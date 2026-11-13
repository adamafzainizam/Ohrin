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
