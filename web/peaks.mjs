// Min/max waveform peaks for one view. Reads only samples.length and integer
// indices, so a very long track never needs copying or slicing.
export function peaksForView(samples, sampleRate, view, bins) {
  const out = new Float32Array(2 * bins);
  const n = samples.length;
  const binDur = (view.end_s - view.start_s) / bins;
  for (let i = 0; i < bins; i++) {
    let a = Math.floor((view.start_s + i * binDur) * sampleRate);
    let b = Math.floor((view.start_s + (i + 1) * binDur) * sampleRate);
    if (b <= a) b = a + 1; // narrower than one sample: read the floor sample
    if (b <= 0 || a >= n) continue; // outside the track: 0, 0
    if (a < 0) a = 0;
    if (b > n) b = n;
    let min = Infinity;
    let max = -Infinity;
    for (let k = a; k < b; k++) {
      const v = samples[k];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    out[2 * i] = min;
    out[2 * i + 1] = max;
  }
  return out;
}
