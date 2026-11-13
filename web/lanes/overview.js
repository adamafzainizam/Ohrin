import { timeToX, xToTime, pan } from '../timeline.mjs';
import { isPrimaryPress, primaryButtonHeld, dragExceeded } from '../state.mjs';

export function createOverviewLane() {
  let el, canvas, box, args, actions, drag = null;
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
    mount(target, callbacks) {
      el = target; actions = callbacks; canvas = el.querySelector('canvas'); box = el.querySelector('.overview-view');
      el.addEventListener('pointerdown', event => {
        if (!args?.state.duration || !isPrimaryPress(event)) return;
        drag = { id: event.pointerId, x: event.clientX, view: args.view, inside: box.contains(event.target), moved: false };
        el.setPointerCapture(event.pointerId);
      });
      el.addEventListener('pointermove', event => {
        if (!drag) return;
        if (event.pointerId !== drag.id || !primaryButtonHeld(event)) { drag = null; return; }
        drag.moved ||= dragExceeded(drag.x, event.clientX);
        if (drag.inside && drag.moved) {
          const width = Math.max(1, el.getBoundingClientRect().width);
          actions.setView(pan(drag.view, (event.clientX - drag.x) / width * args.state.duration, args.state.duration));
        }
      });
      el.addEventListener('pointerup', event => {
        if (!drag || event.pointerId !== drag.id) return;
        const before = drag; drag = null;
        if (before.inside && before.moved) return;
        const rect = el.getBoundingClientRect(), whole = { start_s: 0, end_s: args.state.duration };
        const t = xToTime(Math.max(0, Math.min(rect.width, event.clientX - rect.left)), whole, Math.max(1, rect.width));
        const centre = (before.view.start_s + before.view.end_s) / 2;
        actions.setView(pan(before.view, t - centre, args.state.duration));
      });
      el.addEventListener('pointercancel', () => { drag = null; });
      el.addEventListener('lostpointercapture', () => { drag = null; });
    },
    render(next) { args = next; draw(); },
    reposition(next) { args = { ...args, ...next }; draw(); },
  };
}
