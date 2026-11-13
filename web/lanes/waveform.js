import { sizeCanvas } from '../lanes-model.mjs';
import { timeToX, xToTime } from '../timeline.mjs';
import { peaksForView, buildPeakPyramid } from '../peaks.mjs';
import { clampSelection, resolveDrag, dragExceeded, isPrimaryPress, primaryButtonHeld } from '../state.mjs';

// A single retained mono buffer; peaks are cached by view and CSS pixel width.
export function createWaveformLane() {
  let el, actions, args, samples = null, pyramid = null, sampleRate = 1, cacheKey = '', peaks;
  let drag = null, preview = null, generation = 0;
  function draw() {
    if (!args || !el) return;
    const { state, view, widthPx: w, palette: p } = args;
    const h = 120, ratio = window.devicePixelRatio || 1;
    sizeCanvas(el, w, h, ratio);
    const ctx = el.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const sel = preview || state.selection;
    const left = timeToX(sel.start, view, w), right = timeToX(sel.end, view, w);
    ctx.fillStyle = p.accentFill; ctx.fillRect(left, 0, right - left, h);
    ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(0, h / 2); ctx.lineTo(w, h / 2); ctx.stroke();
    if (samples) {
      const bins = Math.max(1, Math.ceil(w));
      const key = `${view.start_s}:${view.end_s}:${bins}`;
      if (key !== cacheKey) { peaks = peaksForView(samples, sampleRate, view, bins, pyramid); cacheKey = key; }
      for (let i = 0; i < bins; i++) {
        const x = i * w / bins;
        ctx.strokeStyle = x >= left && x <= right ? p.wave : p.waveDim;
        ctx.beginPath(); ctx.moveTo(x + .5, h / 2 - peaks[i * 2 + 1] * h * .43);
        ctx.lineTo(x + .5, h / 2 - peaks[i * 2] * h * .43); ctx.stroke();
      }
    }
    ctx.fillStyle = p.accent;
    for (const x of [left, right]) ctx.fillRect(x - 1, 0, 2, h);
  }
  function time(event) {
    const box = el.getBoundingClientRect();
    return Math.max(0, Math.min(args.state.duration, xToTime(event.clientX - box.left, args.view, box.width)));
  }
  function abandon() { drag = null; preview = null; draw(); }
  return {
    mount(target, callbacks) {
      el = target; actions = callbacks;
      el.addEventListener('pointerdown', event => {
        if (!args?.state.file || !isPrimaryPress(event)) return;
        drag = { id: event.pointerId, x: event.clientX, time: time(event), exceeded: false };
        el.setPointerCapture(event.pointerId);
      });
      el.addEventListener('pointermove', event => {
        if (!drag) return;
        if (event.pointerId !== drag.id || !primaryButtonHeld(event)) { abandon(); return; }
        drag.exceeded ||= dragExceeded(drag.x, event.clientX);
        if (drag.exceeded) { preview = clampSelection(drag.time, time(event), args.state.duration); draw(); }
      });
      el.addEventListener('pointerup', event => {
        if (!drag || event.pointerId !== drag.id) return;
        const end = time(event), before = drag;
        const outcome = resolveDrag({ startX: before.x, endX: event.clientX, startS: before.time, endS: end });
        drag = null; preview = null;
        if (outcome === 'commit') actions.select(before.time, end);
        else { draw(); if (event.button === 0) actions.seek(end); }
      });
      el.addEventListener('pointercancel', abandon);
      el.addEventListener('lostpointercapture', () => { if (drag) abandon(); });
    },
    async loadFile(file) {
      const token = ++generation;
      samples = null; pyramid = null; cacheKey = ''; preview = null; drag = null;
      const context = new AudioContext();
      try {
        const decoded = await context.decodeAudioData(await file.arrayBuffer());
        if (token !== generation) return;
        const mono = new Float32Array(decoded.length);
        for (let c = 0; c < decoded.numberOfChannels; c++) {
          const channel = decoded.getChannelData(c);
          for (let i = 0; i < mono.length; i++) mono[i] += channel[i] / decoded.numberOfChannels;
        }
        pyramid = buildPeakPyramid(mono);
        samples = mono; sampleRate = decoded.sampleRate; draw();
      } finally { await context.close(); }
    },
    clear() { generation++; samples = null; pyramid = null; cacheKey = ''; drag = null; preview = null; },
    render(next) { args = next; draw(); },
    reposition(next) { args = { ...args, ...next }; draw(); },
  };
}
