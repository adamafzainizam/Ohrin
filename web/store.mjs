import { emptyReview } from './state.mjs';

export const TOPICS = Object.freeze(['selection', 'view', 'analysis', 'review', 'ui']);

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
    set(topic, patch) {
      checkTopic(topic);
      state = { ...state, ...patch };
      // One throwing subscriber must not skip the others.
      for (const fn of [...subs.get(topic)]) {
        try { fn(state); } catch (error) { console.error(`Store subscriber for "${topic}" failed:`, error); }
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
