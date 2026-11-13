import { emptyReview } from './state.mjs';

export const TOPICS = Object.freeze(['selection', 'view', 'analysis', 'review', 'ui']);

// Each state key belongs to exactly one topic; set() routes notifications by this map.
export const KEY_TOPIC = Object.freeze({
  selection: 'selection',
  view: 'view',
  analysis: 'analysis',
  analysisRequest: 'analysis',
  review: 'review',
  file: 'ui',
  duration: 'ui',
  selectedBlock: 'ui',
  activeTab: 'ui',
  followPaused: 'ui',
  analysing: 'ui',
});

function defaults() {
  return {
    file: null,
    duration: 0,
    selection: { start: 0, end: 0 },
    view: { start_s: 0, end_s: 0 },
    analysis: null,
    analysisRequest: null,
    review: emptyReview(),
    selectedBlock: null,
    activeTab: 'measured',
    followPaused: false,
    analysing: false,
  };
}

function checkTopic(topic) {
  if (!TOPICS.includes(topic)) throw new TypeError(`Unknown store topic: ${topic}`);
}

export function createStore(initial = {}) {
  let state = { ...defaults(), ...initial };
  const subs = new Map(TOPICS.map(t => [t, []]));
  return {
    get() { return state; },
    // Apply the whole patch, then notify each topic with a changed key once, in TOPICS order.
    set(patch) {
      if (patch === null || typeof patch !== 'object' || Array.isArray(patch)) throw new TypeError('Store patch must be an object');
      const keys = Object.keys(patch);
      for (const key of keys) {
        if (!Object.hasOwn(KEY_TOPIC, key)) throw new TypeError(`Unknown store key: ${key}`);
      }
      const changed = new Set();
      for (const key of keys) if (!Object.is(state[key], patch[key])) changed.add(KEY_TOPIC[key]);
      state = { ...state, ...patch };
      for (const topic of TOPICS) {
        if (!changed.has(topic)) continue;
        // One throwing subscriber must not skip the others.
        for (const fn of [...subs.get(topic)]) {
          try { fn(state); } catch (error) { console.error(`Store subscriber for "${topic}" failed:`, error); }
        }
      }
    },
    subscribe(topic, fn) {
      checkTopic(topic);
      subs.get(topic).push(fn);
      return () => {
        const list = subs.get(topic);
        const i = list.indexOf(fn);
        if (i >= 0) list.splice(i, 1);
      };
    },
  };
}

export function isStale(state) {
  const { analysis, analysisRequest: req, selection: sel } = state;
  return !!analysis && !!req && (sel.start !== req.start || sel.end !== req.end);
}

export function fileLoaded(state, file, duration) {
  return {
    file,
    duration,
    analysis: null,
    analysisRequest: null,
    review: emptyReview(),
    selectedBlock: null,
    followPaused: false,
    view: { start_s: 0, end_s: duration },
  };
}
