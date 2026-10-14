"""Lightweight, reproducible signal probe for a local reference passage.

Requires numpy and ffmpeg. Produces measurements, not instrument/chord labels.
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np


SOURCE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
START = 164.0
SR = 22050
HOP = 512
NFFT = 4096

probe = subprocess.run(
    ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(SOURCE)],
    check=True, capture_output=True, text=True,
)
duration = float(probe.stdout.strip())
decoded = subprocess.run(
    ["ffmpeg", "-v", "error", "-ss", str(START), "-i", str(SOURCE),
     "-map", "0:a:0", "-f", "f32le", "-acodec", "pcm_f32le", "-ar", str(SR), "-ac", "2", "-"],
    check=True, capture_output=True,
)
stereo = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, 2)
mono = stereo.mean(axis=1)
actual_end = START + len(mono) / SR
if len(mono) < SR * 10:
    raise RuntimeError("Too little decoded audio after 164 seconds")

# Framewise short-time spectral measurements.
frames = np.lib.stride_tricks.sliding_window_view(mono, NFFT)[::HOP]
spec = np.abs(np.fft.rfft(frames * np.hanning(NFFT), axis=1)).astype(np.float32)
freq = np.fft.rfftfreq(NFFT, 1 / SR)
times = START + (np.arange(len(spec)) * HOP + NFFT / 2) / SR
power = spec ** 2
bands = {
    "low_40_200": (40, 200),
    "mid_200_2000": (200, 2000),
    "high_2000_8000": (2000, 8000),
}
band_power = {name: power[:, (freq >= lo) & (freq < hi)].sum(axis=1) for name, (lo, hi) in bands.items()}
norm_spec = spec / np.maximum(spec.sum(axis=1, keepdims=True), 1e-8)
flux = np.r_[0, np.maximum(np.diff(norm_spec, axis=0), 0).sum(axis=1)]
flux = flux / max(np.percentile(flux, 95), 1e-8)

# Pitch-class distribution from spectral peaks: exploratory, not a chord estimate.
pitched = (freq >= 80) & (freq <= 1500)
peak = (spec[:, 1:-1] > spec[:, :-2]) & (spec[:, 1:-1] >= spec[:, 2:])
peak = np.pad(peak, ((0, 0), (1, 1))) & pitched[None, :]
midi = np.rint(69 + 12 * np.log2(np.maximum(freq, 1) / 440)).astype(int)
chroma = np.zeros((len(spec), 12), dtype=np.float64)
for pc in range(12):
    mask = peak & (midi[None, :] % 12 == pc)
    chroma[:, pc] = (spec * mask).sum(axis=1)
chroma /= np.maximum(chroma.sum(axis=1, keepdims=True), 1e-8)

windows = []
for lo in np.arange(START, actual_end, 5.0):
    hi = min(lo + 5, actual_end)
    samples = mono[int((lo - START) * SR):int((hi - START) * SR)]
    take = (times >= lo) & (times < hi)
    if not np.any(take) or len(samples) < SR / 2:
        continue
    bp = {k: float(v[take].mean()) for k, v in band_power.items()}
    total = max(sum(bp.values()), 1e-12)
    windows.append({
        "start_s": round(float(lo), 2), "end_s": round(float(hi), 2),
        "rms_dbfs": round(float(20 * np.log10(max(np.sqrt(np.mean(samples ** 2)), 1e-8))), 2),
        "spectral_flux_mean": round(float(flux[take].mean()), 3),
        "band_fraction": {k: round(v / total, 3) for k, v in bp.items()},
        "top_pitch_classes": [int(i) for i in np.argsort(chroma[take].mean(axis=0))[-3:][::-1]],
    })

# Onset-envelope periodicity is reported as candidates, including half/double-time ambiguity.
novelty = flux - np.convolve(flux, np.ones(15) / 15, mode="same")
novelty = np.maximum(novelty, 0)
novelty -= novelty.mean()
ac = np.correlate(novelty, novelty, mode="full")[len(novelty)-1:]
lag_min = round((60 / 190) * SR / HOP)
lag_max = round((60 / 55) * SR / HOP)
candidate_lags = np.argsort(ac[lag_min:lag_max + 1])[-8:][::-1] + lag_min
tempo_candidates = []
for lag in candidate_lags:
    bpm = round(60 * SR / (HOP * lag), 1)
    if all(abs(bpm - item["bpm"]) > 3 for item in tempo_candidates):
        tempo_candidates.append({"bpm": bpm, "autocorrelation": round(float(ac[lag] / max(ac[0], 1e-8)), 3)})

result = {
    "source": str(SOURCE), "start_s": START, "header_duration_s": duration,
    "decoded_end_s": round(actual_end, 3), "sample_rate_hz": SR,
    "pitch_class_order": ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"],
    "tempo_candidates_bpm": tempo_candidates,
    "windows": windows,
    "limitations": "Signal features only; spectral peaks include harmonics and overlapping sources. No listening or instrument/chord identification.",
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({k: result[k] for k in ("decoded_end_s", "tempo_candidates_bpm")}, indent=2))
