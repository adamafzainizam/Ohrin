import test from 'node:test';
import assert from 'node:assert/strict';
import { exportTarget, desktopView } from '../web/desktop.mjs';

test('exportTarget picks native only when save_note is a function', () => {
  assert.equal(exportTarget({ pywebview: { api: { save_note() {} } } }), 'native');
  assert.equal(exportTarget({ pywebview: { api: { save_note: 'x' } } }), 'download');
  assert.equal(exportTarget({ pywebview: { api: {} } }), 'download');
  assert.equal(exportTarget({}), 'download');
  assert.equal(exportTarget(undefined), 'download');
});

test('desktopView shows Quit only in browser mode', () => {
  const found = { found: true, hint: 'x' };
  assert.equal(desktopView({ mode: 'browser', ffmpeg: found }).showQuit, true);
  assert.equal(desktopView({ mode: 'window', ffmpeg: found }).showQuit, false);
  assert.equal(desktopView({ mode: 'dev', ffmpeg: found }).showQuit, false);
});

test('desktopView gates analysis when ffmpeg is missing and includes the hint', () => {
  const view = desktopView({ mode: 'dev', ffmpeg: { found: false, hint: 'sudo apt install ffmpeg' } });
  assert.equal(view.ffmpegMissing, true);
  assert.equal(view.ffmpegMessage, 'FFmpeg is needed to analyse audio. Install it with: sudo apt install ffmpeg');
  const ok = desktopView({ mode: 'dev', ffmpeg: { found: true, hint: 'h' } });
  assert.equal(ok.ffmpegMissing, false);
});

test('desktopView tolerates missing setup', () => {
  for (const setup of [undefined, null, {}, 'oops']) {
    const view = desktopView(setup);
    assert.equal(view.showQuit, false);
    assert.equal(view.ffmpegMissing, false);
  }
});
