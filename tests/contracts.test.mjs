import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const WEB = join(ROOT, 'web');
const read = path => readFileSync(path, 'utf8');

// IDs the scripts build from lists (the enable-on-load list in web/app.js and the EQ editor in web/eq-ui.js).
// Later tasks that add computed IDs append to this list.
export const COMPUTED_IDS = [
  'playButton', 'startTime', 'endTime', 'speed', 'loop', 'analyzeButton', 'eqDisclosure',
  'overviewWrap', 'overview', 'laneStack', 'ruler', 'waveform', 'energyChart', 'playhead',
  'eqType', 'eqFrequency', 'eqGain', 'eqQ', 'eqSlope', 'eqEnabled',
];

function walk(dir) {
  const out = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...walk(path));
    else out.push(path);
  }
  return out;
}

const webFiles = walk(WEB);
const scripts = webFiles.filter(f => /\.(js|mjs)$/.test(f));
const styles = webFiles.filter(f => f.endsWith('.css'));
const indexHtml = read(join(WEB, 'index.html'));

test('ids used by web scripts exist in markup', () => {
  const used = new Set(COMPUTED_IDS);
  const call = /(?:\$|getElementById)\(\s*(['"])([^'"]+)\1\s*\)/g;
  for (const file of scripts) {
    for (const m of read(file).matchAll(call)) used.add(m[2]);
  }
  const defined = new Set();
  const attr = /\bid="([^"]+)"/g;
  for (const m of indexHtml.matchAll(attr)) defined.add(m[1]);
  for (const file of scripts) {
    const src = read(file);
    for (const m of src.matchAll(attr)) defined.add(m[1]);
    for (const m of src.matchAll(/\.id\s*=\s*(['"])([^'"]+)\1/g)) defined.add(m[2]);
  }
  const missing = [...used].filter(id => !defined.has(id)).sort();
  assert.deepEqual(missing, [], `IDs used by scripts but not defined: ${missing.join(', ')}`);
});

function servedPaths() {
  const src = read(join(ROOT, 'ohrin', 'server.py'));
  const block = src.match(/^FILES\s*=\s*\{([\s\S]*?)\}/m);
  assert.ok(block, 'FILES dict literal not found in ohrin/server.py');
  return new Set([...block[1].matchAll(/"([^"]+)"\s*:/g)].map(m => m[1]));
}

const external = ref => /^(data:|blob:|https?:|\/\/|#)/i.test(ref);
const rootRelative = (fromFile, ref) => {
  const clean = ref.split(/[?#]/)[0];
  if (clean.startsWith('/')) return posix.normalize(clean);
  const rel = relative(WEB, join(dirname(fromFile), clean)).split('\\').join('/');
  return '/' + rel;
};

test('every referenced asset is served', () => {
  const served = servedPaths();
  assert.ok(served.size > 0, 'no FILES entries parsed');
  const refs = [];
  const indexPath = join(WEB, 'index.html');
  for (const file of scripts) {
    const src = read(file);
    for (const m of src.matchAll(/\bimport\s+(?:[^'"();]*?\s+from\s+)?(['"])([^'"]+)\1/g)) {
      refs.push([file, m[2]]);
    }
  }
  for (const re of [/<script\b[^>]*\bsrc="([^"]+)"/g, /<link\b[^>]*\bhref="([^"]+)"/g, /<img\b[^>]*\bsrc="([^"]+)"/g]) {
    for (const m of indexHtml.matchAll(re)) refs.push([indexPath, m[1]]);
  }
  for (const file of styles) {
    for (const m of read(file).matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g)) refs.push([file, m[2]]);
  }
  const missing = [];
  for (const [file, ref] of refs) {
    if (external(ref)) continue;
    const path = rootRelative(file, ref);
    if (!served.has(path)) missing.push(`${path} (from ${relative(ROOT, file)})`);
  }
  assert.deepEqual(missing, [], `referenced but not in FILES: ${missing.join('; ')}`);
});

test('app shell regions exist and the landing page is gone', () => {
  const ids = new Set([...indexHtml.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]));
  for (const id of ['topBar', 'moreMenu', 'selectionBar', 'laneArea', 'inspector', 'emptyState', 'timeReadout',
    'audioFile', 'openButton', 'exportButton', 'exportReason', 'eqDisclosure', 'eqPanel', 'selectionLabel']) {
    assert.ok(ids.has(id), `missing #${id}`);
  }
  for (const gone of ['dropZone', 'timeLabel', 'private-pill', 'class="hero"', 'kicker', '<footer']) {
    assert.ok(!indexHtml.includes(gone), `${gone} should be removed`);
  }
  assert.ok(!indexHtml.includes('—'), 'no em-dashes in markup');
  assert.match(indexHtml, /<details id="moreMenu"[^>]*>\s*<summary>More<\/summary>/);
  assert.match(indexHtml, /Open an audio file/);
  assert.match(indexHtml, /Audio stays on this machine/);
});

test('shared axis exposes named zoom and fit controls', () => {
  for (const [id, name] of [['zoomOut', 'Zoom out'], ['zoomIn', 'Zoom in'], ['fitPassage', 'Fit passage'], ['fitTrack', 'Fit track']]) {
    assert.match(indexHtml, new RegExp(`<button[^>]*id="${id}"[^>]*aria-label="${name}"`));
  }
});
