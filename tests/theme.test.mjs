import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parsePalette, readStoredTheme, onInvalidate, invalidate } from '../web/theme.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');

test('parsePalette maps --surface-0 to surface0 and trims whitespace', () => {
  const palette = parsePalette(name => (name === '--surface-0' ? '  #141517 \n' : '#fff'));
  assert.equal(palette.surface0, '#141517');
  assert.equal(palette.lineStrong, '#fff');
  assert.deepEqual(Object.keys(palette).sort(), [
    'accent', 'accentFill', 'dataMeasured', 'focus', 'grid', 'hatch', 'line', 'lineStrong', 'onAccent',
    'playhead', 'surface0', 'surface1', 'surface2', 'text', 'textMuted', 'wave', 'waveDim',
  ]);
});

test('parsePalette throws on an empty --accent', () => {
  assert.throws(() => parsePalette(name => (name === '--accent' ? '   ' : '#fff')), /--accent/);
  assert.throws(() => parsePalette(() => undefined), /--/);
});

test('readStoredTheme falls back to system for anything invalid', () => {
  assert.equal(readStoredTheme({ getItem() { throw new Error('denied'); } }), 'system');
  assert.equal(readStoredTheme({ getItem: () => null }), 'system');
  assert.equal(readStoredTheme({ getItem: () => 'blue' }), 'system');
  assert.equal(readStoredTheme({ getItem: () => 'dark' }), 'dark');
  assert.equal(readStoredTheme({ getItem: () => 'light' }), 'light');
  assert.equal(readStoredTheme(null), 'system');
});

const TOKENS = [
  'surface-0', 'surface-1', 'surface-2', 'line', 'line-strong', 'text', 'text-muted', 'accent', 'on-accent',
  'accent-fill', 'hatch', 'wave', 'data-measured', 'grid', 'wave-dim', 'playhead', 'focus',
];

test('tokens.css defines every role in the dark block and both light blocks', () => {
  const css = readFileSync(join(ROOT, 'web/tokens.css'), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(m => ({ sel: m[1].trim(), body: m[2] }));
  const dark = blocks.find(b => b.sel === ':root' && /--surface-0/.test(b.body));
  const lightMedia = blocks.find(b => b.sel === ':root:not([data-theme="dark"])');
  const lightAttr = blocks.find(b => b.sel === ':root[data-theme="light"]');
  assert.ok(dark && lightMedia && lightAttr, 'dark, media-light and attribute-light blocks exist');
  assert.match(dark.body, /color-scheme:\s*dark/);
  assert.match(lightMedia.body, /color-scheme:\s*light/);
  assert.match(lightAttr.body, /color-scheme:\s*light/);
  assert.match(css, /@media \(prefers-color-scheme: light\)/);
  for (const [name, block] of [['dark', dark], ['light media', lightMedia], ['light attr', lightAttr]]) {
    for (const token of TOKENS) {
      assert.match(block.body, new RegExp(`--${token}:\\s*[^;]+;`), `--${token} in ${name}`);
    }
  }
  assert.deepEqual(lightMedia.body.replace(/\s+/g, ''), lightAttr.body.replace(/\s+/g, ''));
  assert.doesNotMatch(css, /light-dark\(|color-mix\(/);
});

test('a throwing invalidate listener does not skip the others and is reported', t => {
  const errors = [];
  t.mock.method(console, 'error', (...args) => { errors.push(args); });
  const ran = [];
  const offA = onInvalidate(() => { throw new Error('boom'); });
  const offB = onInvalidate(() => ran.push('b'));
  t.after(() => { offA(); offB(); });
  assert.doesNotThrow(() => invalidate());
  assert.deepEqual(ran, ['b']);
  assert.equal(errors.length, 1);
});
