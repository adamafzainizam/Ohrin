// Pure shared-time-axis helpers. A view is { start_s, end_s } in track seconds.
export const MIN_VIEW_S = 2;

function num(value, name) {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new RangeError(`${name} must be a finite number`);
  return value;
}

function checkView(view) {
  if (!view) throw new RangeError('view is required');
  const start = num(view.start_s, 'start_s');
  const end = num(view.end_s, 'end_s');
  if (end <= start) throw new RangeError('view must have a positive span');
  return { start, end };
}

function checkWidth(widthPx) {
  if (num(widthPx, 'widthPx') <= 0) throw new RangeError('widthPx must be positive');
  return widthPx;
}

function checkTrack(track_s) {
  if (num(track_s, 'track_s') <= 0) throw new RangeError('track_s must be positive');
  return track_s;
}

const minSpan = (track_s) => Math.min(MIN_VIEW_S, track_s);

// Place a span of the given length starting near `start`, inside [0, track_s].
function place(start, span, track_s) {
  const s = Math.max(0, Math.min(start, track_s - span));
  return { start_s: s, end_s: s + span };
}

export function timeToX(t, view, widthPx) {
  const { start, end } = checkView(view);
  checkWidth(widthPx);
  return ((num(t, 't') - start) / (end - start)) * widthPx;
}

export function xToTime(x, view, widthPx) {
  const { start, end } = checkView(view);
  checkWidth(widthPx);
  return start + (num(x, 'x') / widthPx) * (end - start);
}

export function fit(start_s, end_s, track_s) {
  num(start_s, 'start_s');
  num(end_s, 'end_s');
  checkTrack(track_s);
  const a = Math.max(0, Math.min(track_s, Math.min(start_s, end_s)));
  const b = Math.max(0, Math.min(track_s, Math.max(start_s, end_s)));
  const span = Math.min(track_s, Math.max(b - a, minSpan(track_s)));
  return place((a + b) / 2 - span / 2, span, track_s);
}

export function zoomAt(view, anchor_s, factor, track_s) {
  const { start, end } = checkView(view);
  num(anchor_s, 'anchor_s');
  if (num(factor, 'factor') <= 0) throw new RangeError('factor must be positive');
  checkTrack(track_s);
  const span = Math.min(track_s, Math.max((end - start) / factor, minSpan(track_s)));
  const fraction = (anchor_s - start) / (end - start);
  return place(anchor_s - fraction * span, span, track_s);
}

export function pan(view, delta_s, track_s) {
  const { start, end } = checkView(view);
  num(delta_s, 'delta_s');
  checkTrack(track_s);
  return place(start + delta_s, Math.min(end - start, track_s), track_s);
}

export function followPage(view, t, track_s) {
  const { start, end } = checkView(view);
  num(t, 't');
  checkTrack(track_s);
  if (t >= start && t <= end) return view;
  return place(t, Math.min(end - start, track_s), track_s);
}

export function formatTenths(s) {
  const tenths = Math.max(0, Math.round(num(s, 's') * 10));
  const minutes = Math.floor(tenths / 600);
  const seconds = Math.floor((tenths % 600) / 10);
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}.${tenths % 10}`;
}
