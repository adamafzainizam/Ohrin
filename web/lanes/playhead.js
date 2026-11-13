import { timeToX } from '../timeline.mjs';

export function createPlayhead() {
  let el, view, widthPx = 1, time = 0;
  function move() {
    if (!el || !view) return;
    if (view.end_s <= view.start_s) { el.hidden = true; return; }
    el.hidden = time < view.start_s || time > view.end_s;
    el.style.transform = `translateX(${timeToX(time, view, widthPx)}px)`;
  }
  return {
    mount(target) { el = target; },
    render(args) { view = args.view; widthPx = args.widthPx; move(); },
    reposition(args) { view = args.view; widthPx = args.widthPx; move(); },
    move(t) { time = t; move(); },
  };
}
