// Pure lane descriptors built from the analysis response. All times are track seconds.
import { instrumentTimeline } from './state.mjs';

export const ENERGY_LABEL = 'Measured';
export const MODEL_LABEL = 'Model output, uncertain';

export function lanesFromAnalysis(analysis) {
  const windows = Array.isArray(analysis?.windows) ? analysis.windows : [];
  const lanes = [{ id: 'energy', provenance: 'measured', label: ENERGY_LABEL, items: windows }];
  const timeline = instrumentTimeline(analysis?.instruments, analysis?.passage);
  if (!timeline) return lanes;
  for (const row of timeline.rows) {
    lanes.push({
      id: `inst:${row.name}`, provenance: 'model', label: MODEL_LABEL,
      className: row.name, threshold: row.threshold,
      items: row.cells.map(({ start_s, end_s, probability, above, short }) =>
        ({ start_s, end_s, probability, above, short })),
    });
  }
  return lanes;
}

export function notAnalysed(analysis) {
  const timeline = instrumentTimeline(analysis?.instruments, analysis?.passage);
  if (!timeline) return [];
  return timeline.uncovered.map(({ start_s, end_s }) => ({ start_s, end_s }));
}
