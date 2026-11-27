import test from 'node:test';
import assert from 'node:assert/strict';
import { setupView, formatBytes } from '../web/setup.mjs';

const assets = [
  { name: 'vggish-10086976.pth', present: false, verified: false, bytes: 0, size: 288567937 },
  { name: 'vggish_pca_params.npz', present: false, verified: false, bytes: 0, size: 73020 },
  { name: 'openmic-mlp.pt', present: false, verified: false, bytes: 0, size: 36805 },
];
const setup = (extra = {}) => ({
  mode: 'window', data_dir: '/home/u/.local/share/Ohrin', torch: true, assets, downloadable: true,
  download_bytes: 288677762, hosts: ['github.com (release download)', 'storage.googleapis.com'], ...extra,
});
const idle = { state: 'idle', file: null, bytes: 0, total: 0, error: null };

test('setupView shows consent and sizes before download', () => {
  const v = setupView(setup(), idle, { dismissed: false });
  assert.equal(v.visible, true);
  assert.equal(v.canDownload, true);
  assert.equal(v.sizeText, '289 MB');
  assert.deepEqual(v.hosts, ['github.com (release download)', 'storage.googleapis.com']);
  assert.equal(v.destination, '/home/u/.local/share/Ohrin');
  assert.equal(v.showCancel, false);
  assert.equal(v.showRetry, false);
  assert.match(v.message, /No audio or usage data is sent/);
  assert.equal(formatBytes(73020), '73 KB');
  assert.equal(formatBytes(36805), '37 KB');
  assert.equal(formatBytes(288567937), '289 MB');
  assert.equal(formatBytes(1500000), '1.5 MB');
});

test('setupView never offers download without torch', () => {
  const v = setupView(setup({ torch: false }), idle, {});
  assert.equal(v.visible, true);
  assert.equal(v.canDownload, false);
  assert.match(v.message, /Instrument candidates need PyTorch, which this installation does not have/);
});

test('setupView explains an unpublished head', () => {
  const v = setupView(setup({ downloadable: false }), idle, {});
  assert.equal(v.visible, true);
  assert.equal(v.canDownload, false);
  assert.match(v.message, /not available yet/);
});

test('setupView maps checksum error to discard message with retry', () => {
  const v = setupView(setup(), { ...idle, state: 'error', error: 'Checksum mismatch, file discarded.' }, {});
  assert.match(v.message, /Checksum mismatch, file discarded/);
  assert.equal(v.showRetry, true);
  assert.equal(v.canDownload, false);
  assert.equal(v.showCancel, false);
  const c = setupView(setup(), { ...idle, state: 'cancelled' }, {});
  assert.equal(c.showRetry, true);
  assert.equal(c.message, 'Download cancelled. Files that already verified were kept; the incomplete file was discarded.');
  const d = setupView(setup({ assets: assets.map(a => ({ ...a, verified: true })), download_bytes: 0 }), { ...idle, state: 'done' }, {});
  assert.equal(d.visible, true);
  assert.match(d.message, /Verified/);
  assert.match(d.message, /next analysis/);
});

test('progress percent clamps and handles unknown total', () => {
  const dl = (bytes, total) => setupView(setup(), { state: 'downloading', file: 'a.pth', bytes, total, error: null }, {});
  assert.equal(dl(50, 200).percent, 25);
  assert.equal(dl(500, 200).percent, 100);
  assert.equal(dl(-5, 200).percent, 0);
  assert.equal(dl(10, 0).percent, null);
  assert.equal(dl(10, NaN).percent, null);
  assert.equal(dl(50, 200).showCancel, true);
  assert.match(dl(50, 200).valueText, /25 percent/);
  assert.doesNotMatch(dl(10, 0).valueText, /percent/);
});

test('setupView hides after dismissal for the session', () => {
  assert.equal(setupView(setup(), idle, { dismissed: true }).visible, false);
  assert.equal(setupView(setup({ assets: assets.map(a => ({ ...a, verified: true })), download_bytes: 0 }), idle, {}).visible, false);
  assert.equal(setupView(null, idle, {}).visible, false);
  const running = { state: 'downloading', file: 'a', bytes: 1, total: 2, error: null };
  assert.equal(setupView(setup(), running, { dismissed: true }).visible, true);
});
