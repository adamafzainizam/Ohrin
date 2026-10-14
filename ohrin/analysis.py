"""Exploratory analysis of a passage in a mixed recording.

All harmonic and tempo outputs are candidates. No instrument separation occurs.
"""

from pathlib import Path
import subprocess

import numpy as np


SR = 22050
FFT = 4096
HOP = 512
PITCH_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _run(args):
    return subprocess.run(args, capture_output=True, check=False)


def _candidates(chroma):
    if not len(chroma) or not np.any(chroma):
        return []
    average = chroma.mean(axis=0)
    results = []
    for root in range(12):
        for quality, intervals, shape in (
            ("major", (0, 4, 7), (0, 2, 2, 1, 0, 0)),
            ("minor", (0, 3, 7), (0, 2, 2, 0, 0, 0)),
        ):
            template = np.zeros(12)
            template[[(root + i) % 12 for i in intervals]] = 1
            score = float(np.dot(average, template) / max(np.linalg.norm(average) * np.linalg.norm(template), 1e-8))
            fret = (root - 4) % 12
            results.append({
                "name": f"{PITCH_NAMES[root]} {quality}",
                "score": round(score, 3),
                "status": "uncertain",
                "suggested_standard_tuning_frets": [fret + offset for offset in shape],
            })
    return sorted(results, key=lambda row: row["score"], reverse=True)[:3]


def _tempo_candidates(flux):
    if len(flux) < 80 or max(flux) < 1e-8:
        return []
    novelty = np.maximum(flux - np.convolve(flux, np.ones(15) / 15, mode="same"), 0)
    novelty -= novelty.mean()
    ac = np.correlate(novelty, novelty, mode="full")[len(novelty) - 1:]
    lag_min = round((60 / 190) * SR / HOP)
    lag_max = min(round((60 / 55) * SR / HOP), len(ac) - 1)
    if lag_max <= lag_min or ac[0] <= 0:
        return []
    peaks = np.argsort(ac[lag_min:lag_max + 1])[-8:][::-1] + lag_min
    results = []
    for lag in peaks:
        bpm = round(60 * SR / (HOP * lag), 1)
        if all(abs(bpm - row["bpm"]) > 3 for row in results):
            results.append({"bpm": bpm, "periodicity": round(float(ac[lag] / ac[0]), 3)})
    return results[:4]


def analyze_audio(path: Path, start_s: float, end_s: float) -> dict:
    """Return local measurements and explicitly tentative musical candidates."""
    if not np.isfinite(start_s) or not np.isfinite(end_s) or start_s < 0 or end_s <= start_s:
        raise ValueError("Select a passage with a valid start and end.")
    probe = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)])
    try:
        duration = float(probe.stdout.decode().strip())
    except ValueError as exc:
        raise ValueError("This audio file could not be read.") from exc
    if duration <= start_s:
        raise ValueError("The selection begins after the recording ends.")
    end_s = min(end_s, duration)
    if end_s - start_s > 180:
        raise ValueError("Select a passage no longer than three minutes.")
    if end_s - start_s < 1:
        raise ValueError("Select at least one second of audio.")
    decoded = _run([
        "ffmpeg", "-v", "error", "-ss", str(start_s), "-i", str(path),
        "-t", str(end_s - start_s), "-map", "0:a:0", "-f", "f32le",
        "-acodec", "pcm_f32le", "-ar", str(SR), "-ac", "2", "-",
    ])
    if decoded.returncode or len(decoded.stdout) < SR * 2 * 4:
        raise ValueError("This passage could not be decoded; the file may be incomplete.")
    stereo = np.frombuffer(decoded.stdout, dtype="<f4").reshape(-1, 2)
    mono = stereo.mean(axis=1)
    actual_end = min(end_s, start_s + len(mono) / SR)
    if len(mono) < FFT:
        raise ValueError("This passage is too short to analyse.")

    frames = np.lib.stride_tricks.sliding_window_view(mono, FFT)[::HOP]
    spec = np.abs(np.fft.rfft(frames * np.hanning(FFT), axis=1)).astype(np.float32)
    freq = np.fft.rfftfreq(FFT, 1 / SR)
    times = start_s + (np.arange(len(spec)) * HOP + FFT / 2) / SR
    power = spec ** 2
    bands = ((40, 200), (200, 2000), (2000, 8000))
    band_power = [power[:, (freq >= lo) & (freq < hi)].sum(axis=1) for lo, hi in bands]
    norm = spec / np.maximum(spec.sum(axis=1, keepdims=True), 1e-8)
    flux = np.r_[0, np.maximum(np.diff(norm, axis=0), 0).sum(axis=1)]
    flux = flux / max(float(np.percentile(flux, 95)), 1e-8)

    peak = np.pad((spec[:, 1:-1] > spec[:, :-2]) & (spec[:, 1:-1] >= spec[:, 2:]), ((0, 0), (1, 1)))
    peak &= ((freq >= 80) & (freq <= 1500))[None, :]
    midi = np.rint(69 + 12 * np.log2(np.maximum(freq, 1) / 440)).astype(int)
    chroma = np.zeros((len(spec), 12), dtype=np.float64)
    for pc in range(12):
        chroma[:, pc] = (spec * (peak & (midi[None, :] % 12 == pc))).sum(axis=1)
    chroma /= np.maximum(chroma.sum(axis=1, keepdims=True), 1e-8)

    windows = []
    for lo in np.arange(start_s, actual_end, 1.0):
        hi = min(float(lo + 1), actual_end)
        samples = mono[int((lo - start_s) * SR):int((hi - start_s) * SR)]
        take = (times >= lo) & (times < hi)
        if len(samples) < SR / 2 or not np.any(take):
            continue
        bp = np.array([float(v[take].mean()) for v in band_power])
        bp /= max(float(bp.sum()), 1e-12)
        windows.append({
            "start_s": round(float(lo), 3), "end_s": round(hi, 3),
            "rms_dbfs": round(float(20 * np.log10(max(np.sqrt(np.mean(samples ** 2)), 1e-8))), 2),
            "onset_change": round(float(flux[take].mean()), 3),
            "band_fraction": [round(float(v), 3) for v in bp],
        })
    return {
        "duration_s": round(duration, 3),
        "passage": {"start_s": round(start_s, 3), "end_s": round(actual_end, 3)},
        "windows": windows,
        "tempo_candidates": _tempo_candidates(flux),
        "candidate_chords": _candidates(chroma),
        "limitations": [
            "Level, onset change, and frequency balance are measured from the mixed signal.",
            "Tempo and chord names are experimental candidates, not validated transcriptions.",
            "Suggested fret shapes use standard tuning and are not evidence of the original performance.",
            "Instrument identity and activity are not inferred by this analysis.",
        ],
    }
