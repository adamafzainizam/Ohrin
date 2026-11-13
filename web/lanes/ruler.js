import { sizeCanvas } from '../lanes-model.mjs';
import { timeToX, formatTenths } from '../timeline.mjs';
import { CANVAS_FONT } from '../theme.mjs';

export function createRulerLane() {
  let el, args;
  function draw() {
    if (!args) return;
    const { view, widthPx: w, palette: p } = args, h = 32, ratio = window.devicePixelRatio || 1;
    sizeCanvas(el, w, h, ratio);
    const ctx = el.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h); ctx.font = CANVAS_FONT;
    const span = view.end_s - view.start_s;
    if (span <= 0) return;
    const target = span / Math.max(1, w / 90);
    const magnitude = 10 ** Math.floor(Math.log10(target));
    const step = [1, 2, 5, 10].map(n => n * magnitude).find(n => n >= target);
    for (let t = Math.ceil(view.start_s / step) * step; t <= view.end_s; t += step) {
      const x = timeToX(t, view, w);
      ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(x + .5, 24); ctx.lineTo(x + .5, h); ctx.stroke();
      ctx.fillStyle = p.textMuted; ctx.fillText(formatTenths(t), Math.max(0, Math.min(w - 62, x + 3)), 17);
    }
  }
  return { mount(target) { el = target; }, render(next) { args = next; draw(); }, reposition(next) { args = { ...args, ...next }; draw(); } };
}
