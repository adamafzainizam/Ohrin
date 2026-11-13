// Min/max waveform peaks for one view. Reads only samples.length and integer
// indices, so a very long track never needs copying or slicing.

// Precomputed min/max summary: level 0 covers `baseBlock` samples per entry, and
// each further level combines `factor` entries of the one below.
export function buildPeakPyramid(samples, baseBlock = 256, factor = 4) {
  const n = samples.length;
  const count0 = Math.max(1, Math.ceil(n / baseBlock));
  let mins = new Float32Array(count0);
  let maxs = new Float32Array(count0);
  for (let j = 0; j < count0; j++) {
    const end = Math.min(n, (j + 1) * baseBlock);
    let min = Infinity, max = -Infinity;
    for (let k = j * baseBlock; k < end; k++) {
      const v = samples[k];
      if (v < min) min = v;
      if (v > max) max = v;
    }
    mins[j] = n === 0 ? 0 : min;
    maxs[j] = n === 0 ? 0 : max;
  }
  const levels = [{ block: baseBlock, mins, maxs }];
  while (mins.length > 512) {
    const count = Math.ceil(mins.length / factor);
    if (count <= 1) break;
    const nextMins = new Float32Array(count), nextMaxs = new Float32Array(count);
    for (let j = 0; j < count; j++) {
      const end = Math.min(mins.length, (j + 1) * factor);
      let min = Infinity, max = -Infinity;
      for (let k = j * factor; k < end; k++) {
        if (mins[k] < min) min = mins[k];
        if (maxs[k] > max) max = maxs[k];
      }
      nextMins[j] = min; nextMaxs[j] = max;
    }
    mins = nextMins; maxs = nextMaxs;
    levels.push({ block: levels[levels.length - 1].block * factor, mins, maxs });
  }
  return { baseBlock, factor, length: n, levels };
}

export function peaksForView(samples, sampleRate, view, bins, pyramid) {
  const out = new Float32Array(2 * bins);
  const n = samples.length;
  const binDur = (view.end_s - view.start_s) / bins;
  for (let i = 0; i < bins; i++) {
    let a = Math.floor((view.start_s + i * binDur) * sampleRate);
    let b = Math.floor((view.start_s + (i + 1) * binDur) * sampleRate);
    if (b <= a) b = a + 1; // narrower than one sample: read the floor sample
    if (b <= 0 || a >= n) continue; // outside the track: 0, 0
    const span = b - a;
    if (a < 0) a = 0;
    if (b > n) b = n;
    let min = Infinity;
    let max = -Infinity;
    let level = null;
    if (pyramid) {
      for (const l of pyramid.levels) if (l.block * 2 <= span) level = l;
    }
    if (level) {
      const first = Math.floor(a / level.block);
      const last = Math.min(level.mins.length - 1, Math.floor((b - 1) / level.block));
      for (let k = first; k <= last; k++) {
        if (level.mins[k] < min) min = level.mins[k];
        if (level.maxs[k] > max) max = level.maxs[k];
      }
    } else {
      for (let k = a; k < b; k++) {
        const v = samples[k];
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    out[2 * i] = min;
    out[2 * i + 1] = max;
  }
  return out;
}
