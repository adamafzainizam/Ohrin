import { sizeCanvas } from '../lanes-model.mjs';
import { timeToX } from '../timeline.mjs';
import { lanesFromAnalysis } from '../lanes-model.mjs';
import { CANVAS_FONT } from '../theme.mjs';

export function createEnergyLane() {
  let el, args;
  function draw() {
    if (!args) return;
    const { state, view, widthPx: w, palette: p } = args, h = 64, ratio = window.devicePixelRatio || 1;
    sizeCanvas(el, w, h, ratio);
    const ctx = el.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h); ctx.font = CANVAS_FONT;
    if (state.analysing) { ctx.fillStyle = p.surface2; ctx.fillRect(0, 8, w, h - 16); return; }
    const passage = state.analysis?.passage;
    const gaps = passage ? [[view.start_s, passage.start_s], [passage.end_s, view.end_s]] : [[view.start_s, view.end_s]];
    for (const [a, b] of gaps) {
      const left = Math.max(0, timeToX(a, view, w)), right = Math.min(w, timeToX(b, view, w));
      if (right <= left) continue;
      ctx.fillStyle = p.surface2; ctx.fillRect(left, 0, right - left, h);
      if (right - left > 85) { ctx.fillStyle = p.textMuted; ctx.fillText('Not analysed', left + 6, h / 2 + 4); }
    }
    const items = lanesFromAnalysis(state.analysis)[0].items;
    ctx.lineWidth = 1;
    for (const db of [-30, -20, -10]) {
      const y = 4 + -db / 40 * 56;
      ctx.strokeStyle = p.grid; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
    }
    ctx.beginPath();
    items.forEach((item, i) => {
      const x = timeToX((item.start_s + item.end_s) / 2, view, w);
      const y = 4 + Math.max(0, Math.min(1, -item.rms_dbfs / 40)) * 56;
      if (i) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    });
    ctx.strokeStyle = state.findingsStale ? p.waveDim : p.dataMeasured; ctx.lineWidth = 2; ctx.stroke();
  }
  return { mount(target) { el = target; }, render(next) { args = next; draw(); }, reposition(next) { args = { ...args, ...next }; draw(); } };
}
