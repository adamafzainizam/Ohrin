// Theme choice, palette access and the shared redraw signal for canvases.
// parsePalette and readStoredTheme are pure; everything else needs a browser.

export const THEME_KEY = 'ohrin.theme';
export const CHOICES = ['system', 'light', 'dark'];
export const CANVAS_FONT = '11px "Geist Mono", ui-monospace, monospace';

const PALETTE_TOKENS = {
  surface0: '--surface-0', surface1: '--surface-1', surface2: '--surface-2',
  line: '--line', lineStrong: '--line-strong', text: '--text', textMuted: '--text-muted',
  accent: '--accent', onAccent: '--on-accent', accentFill: '--accent-fill', hatch: '--hatch',
  wave: '--wave', waveDim: '--wave-dim', dataMeasured: '--data-measured', grid: '--grid',
  playhead: '--playhead', focus: '--focus',
};

export function parsePalette(getter) {
  const palette = {};
  for (const [key, name] of Object.entries(PALETTE_TOKENS)) {
    const value = String(getter(name) ?? '').trim();
    if (!value) throw new Error(`Theme token ${name} is empty.`);
    palette[key] = value;
  }
  return palette;
}

export function readStoredTheme(storage) {
  try {
    const value = storage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

const listeners = new Set();
let cached = null;
let observer = null;
let initialised = false;

export function onInvalidate(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function invalidate() {
  cached = null;
  for (const fn of [...listeners]) {
    try { fn(); } catch (error) { console.error('Theme redraw listener failed:', error); }
  }
}

export function getPalette() {
  if (!cached) {
    const style = getComputedStyle(document.documentElement);
    cached = parsePalette(name => style.getPropertyValue(name));
  }
  return cached;
}

export function observeCanvasHost(el) {
  if (typeof ResizeObserver === 'undefined' || !el) return;
  if (!observer) observer = new ResizeObserver(() => invalidate());
  observer.observe(el);
}

function updateThemeColor() {
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', getPalette().surface0);
}

function storedChoice() {
  try { return readStoredTheme(window.localStorage); } catch { return 'system'; }
}

function syncControl(choice) {
  for (const button of document.querySelectorAll('[data-theme-choice]')) {
    const on = button.dataset.themeChoice === choice;
    button.setAttribute('aria-checked', String(on));
    button.tabIndex = on ? 0 : -1;
  }
}

export function applyTheme(choice) {
  const next = CHOICES.includes(choice) ? choice : 'system';
  const root = document.documentElement;
  if (next === 'system') root.removeAttribute('data-theme');
  else root.dataset.theme = next;
  try {
    if (next === 'system') window.localStorage.removeItem(THEME_KEY);
    else window.localStorage.setItem(THEME_KEY, next);
  } catch { /* storage may be blocked; the choice then lasts for this page only */ }
  syncControl(next);
  invalidate();
}

function watchDpr() {
  const query = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
  query.addEventListener('change', () => { invalidate(); watchDpr(); }, { once: true });
}

// Wires the theme buttons and every redraw trigger except per-canvas observers.
export function initTheme() {
  if (initialised) return;
  initialised = true;
  const root = document.documentElement;
  const choice = storedChoice();
  if (choice === 'system') root.removeAttribute('data-theme'); else root.dataset.theme = choice;
  syncControl(choice);
  onInvalidate(updateThemeColor);
  const buttons = [...document.querySelectorAll('[data-theme-choice]')];
  buttons.forEach((button, index) => {
    button.addEventListener('click', () => applyTheme(button.dataset.themeChoice));
    button.addEventListener('keydown', event => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const target = buttons[(index + step + buttons.length) % buttons.length];
      applyTheme(target.dataset.themeChoice);
      target.focus();
    });
  });
  window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => invalidate());
  watchDpr();
  if (document.fonts) {
    document.fonts.ready.then(() => invalidate());
    Promise.all([
      document.fonts.load('400 11px "Geist Mono"'), document.fonts.load('400 13px "Geist"'),
    ]).then(() => invalidate(), () => {});
  }
  updateThemeColor();
}
