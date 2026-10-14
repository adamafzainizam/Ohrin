export function clampSelection(a, b, duration) {
  const limit = Math.max(0, Number(duration) || 0);
  const x = Math.max(0, Math.min(limit, Number(a) || 0));
  const y = Math.max(0, Math.min(limit, Number(b) || 0));
  return { start: Math.min(x, y), end: Math.max(x, y) };
}

export function shouldLoop(time, selection, enabled) {
  return Boolean(enabled && selection.end > selection.start && time >= selection.end);
}

export function shouldStop(time, selection, loopEnabled) {
  return Boolean(!loopEnabled && selection.end > selection.start && time >= selection.end);
}

export function formatTime(seconds) {
  const n = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(n / 60)).padStart(2, '0')}:${String(n % 60).padStart(2, '0')}`;
}

export function measuredObservations(windows) {
  if (!Array.isArray(windows) || windows.length < 2) return [];
  let largest = null;
  for (let i = 1; i < windows.length; i++) {
    const drop = windows[i - 1].rms_dbfs - windows[i].rms_dbfs;
    if (!largest || drop > largest.drop) largest = { at: windows[i].start_s, drop };
  }
  const remarks = [];
  if (largest.drop >= 3) remarks.push(`At ${formatTime(largest.at)}, one-second RMS level fell ${largest.drop.toFixed(1)} dB from the previous window.`);
  const first = windows[0].band_fraction || [];
  const last = windows.at(-1).band_fraction || [];
  if (first.length === 3 && last.length === 3) {
    const highChange = Math.round((last[2] - first[2]) * 100);
    if (Math.abs(highChange) >= 5) remarks.push(`High-frequency power share changed ${highChange > 0 ? '+' : ''}${highChange} percentage points from the first to last analysed second.`);
  }
  return remarks;
}

export const SEGMENT_NOMINAL_S = 9.6;
const EDGE_TOLERANCE_S = 0.05;

export function timeToFraction(t, passage) {
  const start = Number(passage?.start_s);
  const end = Number(passage?.end_s);
  const value = Number(t);
  if (!Number.isFinite(start) || !Number.isFinite(end) || !Number.isFinite(value) || end - start <= 0) return 0;
  return Math.max(0, Math.min(1, (value - start) / (end - start)));
}

export function instrumentTimeline(instruments, passage) {
  if (!instruments?.available || !Array.isArray(instruments.segments) || !instruments.segments.length) return null;
  const segments = instruments.segments;
  const classes = Array.isArray(instruments.classes) ? instruments.classes : [];
  const rows = classes.map(c => ({
    name: c.name,
    threshold: c.threshold,
    cells: segments.map((s, index) => {
      const raw = s.probabilities?.[c.name];
      const probability = typeof raw === 'number' && Number.isFinite(raw) ? raw : null;
      const left = timeToFraction(s.start_s, passage);
      const right = timeToFraction(s.end_s, passage);
      return {
        index, start_s: s.start_s, end_s: s.end_s, probability,
        above: probability !== null && probability >= c.threshold,
        short: s.end_s - s.start_s < SEGMENT_NOMINAL_S - EDGE_TOLERANCE_S,
        left, width: Math.max(0, right - left),
      };
    }),
  }));
  const uncovered = [];
  const pStart = Number(passage?.start_s), pEnd = Number(passage?.end_s);
  if (Number.isFinite(pStart) && Number.isFinite(pEnd)) {
    let cursor = pStart;
    for (const s of [...segments].sort((a, b) => a.start_s - b.start_s)) {
      if (s.start_s - cursor > EDGE_TOLERANCE_S) uncovered.push({ start_s: cursor, end_s: s.start_s });
      cursor = Math.max(cursor, s.end_s);
    }
    if (pEnd - cursor > EDGE_TOLERANCE_S) uncovered.push({ start_s: cursor, end_s: pEnd });
  }
  const shortCount = segments.filter(s => s.end_s - s.start_s < SEGMENT_NOMINAL_S - EDGE_TOLERANCE_S).length;
  return { resolution_s: SEGMENT_NOMINAL_S, rows, uncovered, shortCount };
}

export const LABELS = Object.freeze(['heard', 'not_heard', 'unsure']);
const TARGET_PATTERN = /^(class:[a-z]+|seg:\d+:[a-z]+)$/;

export function emptyReview(key = null) { return { key, labels: {} }; }

export function reviewKey(fileMeta, passage) {
  if (!fileMeta || !passage) return null;
  const { name, size, lastModified } = fileMeta;
  const { start_s, end_s } = passage;
  const parts = [name, size, lastModified, start_s, end_s];
  if (parts.some(p => p === undefined || p === null || p === '')) return null;
  return parts.join('|');
}

export function candidateTarget(name) { return `class:${name}`; }
export function segmentTarget(index, name) { return `seg:${index}:${name}`; }

export function setLabel(review, target, label) {
  if (typeof target !== 'string' || !TARGET_PATTERN.test(target)) throw new TypeError(`Invalid review target: ${target}`);
  const labels = { ...review.labels };
  if (label === null) delete labels[target];
  else if (LABELS.includes(label)) labels[target] = label;
  else throw new TypeError(`Invalid review label: ${label}`);
  return { key: review.key, labels };
}

export function toggleLabel(review, target, label) {
  return setLabel(review, target, review.labels[target] === label ? null : label);
}

export function reconcileReview(review, key) {
  return key !== null && review.key === key ? review : emptyReview(key);
}

export function pruneReview(review, instruments) {
  const names = new Set(instruments?.available && Array.isArray(instruments.classes) ? instruments.classes.map(c => c.name) : []);
  const count = instruments?.available && Array.isArray(instruments.segments) ? instruments.segments.length : 0;
  const labels = {};
  for (const [target, label] of Object.entries(review.labels)) {
    const [kind, a, b] = target.split(':');
    if (kind === 'class' ? names.has(a) : (names.has(b) && Number(a) < count)) labels[target] = label;
  }
  return { key: review.key, labels };
}

export function reviewSummary(review) {
  const out = { heard: 0, not_heard: 0, unsure: 0, total: 0 };
  for (const label of Object.values(review.labels)) { out[label] += 1; out.total += 1; }
  return out;
}

export function isStaleResponse(requested, current) {
  return requested.file !== current.file
    || requested.selection.start !== current.selection.start
    || requested.selection.end !== current.selection.end;
}

export const NOTE_SCHEMA = 'ohrin.passage-note';
export const NOTE_SCHEMA_VERSION = 2;
const MARKS_LIMITATION = 'Listening marks are user judgements, not validation of the model.';

function listeningReview(instruments, review) {
  if (!instruments?.available) return null;
  const classes = instruments.classes || [];
  const segments = instruments.segments || [];
  const candidates = classes.map(c => ({
    instrument: c.name, model_score: c.probability, model_threshold: c.threshold,
    model_above_threshold: c.above_threshold, your_label: review.labels[candidateTarget(c.name)] ?? null,
  }));
  const marked = [];
  segments.forEach((s, index) => {
    for (const c of classes) {
      const label = review.labels[segmentTarget(index, c.name)];
      if (!label) continue;
      const score = s.probabilities?.[c.name];
      marked.push({
        index, start_s: s.start_s, end_s: s.end_s, instrument: c.name,
        model_score: typeof score === 'number' ? score : null,
        model_above_threshold: typeof score === 'number' && score >= c.threshold,
        your_label: label,
      });
    }
  });
  return {
    about: "The user's own listening judgements. Not ground truth; not sent anywhere by Ohrin; not used to train the model.",
    scale: [...LABELS], candidates, segments: marked, summary: reviewSummary(review),
  };
}

export function buildPassageNote({ fileName, analysis, chordCorrection, arrangementNotes, review, exportedAt }) {
  const limitations = Array.isArray(analysis.limitations) ? [...analysis.limitations] : [];
  if (Object.keys(review.labels).length) limitations.push(MARKS_LIMITATION);
  return {
    schema: NOTE_SCHEMA, schema_version: NOTE_SCHEMA_VERSION, app: 'Ohrin', exported_at: exportedAt,
    file_name: fileName, passage: analysis.passage,
    measured_observations: measuredObservations(analysis.windows),
    tempo_candidates: analysis.tempo_candidates, harmonic_candidates: analysis.candidate_chords,
    chord_correction: chordCorrection, arrangement_notes: arrangementNotes,
    instrument_candidates: analysis.instruments ?? null,
    listening_review: listeningReview(analysis.instruments, review),
    limitations,
  };
}
