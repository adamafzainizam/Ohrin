import { timeToX } from '../timeline.mjs';

export function createOverviewLane() {
  let el, canvas, box, args;
  function draw() {
    if (!args) return;
    const { state, view, widthPx: w, palette: p } = args;
    const ratio = window.devicePixelRatio || 1, h = 36;
    canvas.width = Math.round(w * ratio); canvas.height = h * ratio;
    const ctx = canvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.fillStyle = p.surface2; ctx.fillRect(0, 0, w, h);
    const whole = { start_s: 0, end_s: Math.max(1, state.duration) };
    const left = timeToX(state.selection.start, whole, w), right = timeToX(state.selection.end, whole, w);
    ctx.fillStyle = p.accentFill; ctx.fillRect(left, 0, right - left, h);
    ctx.strokeStyle = p.accent; ctx.strokeRect(left, 1, Math.max(1, right - left), h - 2);
    box.style.left = `${timeToX(view.start_s, whole, w)}px`;
    box.style.width = `${Math.max(1, timeToX(view.end_s, whole, w) - timeToX(view.start_s, whole, w))}px`;
  }
  return {
    mount(target) {
      el = target; canvas = el.querySelector('canvas'); box = el.querySelector('.overview-view');
    },
    render(next) { args = next; draw(); },
    reposition(next) { args = { ...args, ...next }; draw(); },
  };
}
