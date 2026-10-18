"""Does the shipped drums/bass head lose recall when guitar or bass is mixed in? Masking curve on OpenMIC test clips.

Spec items 0-pre(i) (feature parity) and 0c (masking curve) plus Gate 0. Run from project root:
  python3 -m experiments.masking_curve parity                 # 8-clip audio_subset parity, no archive needed
  python3 -m experiments.masking_curve extract                # clips for both pair modes plus parity clips; exit codes below
  python3 -m experiments.masking_curve run                    # parity + masking curve + Gate 0 (strict pairs, primary)
  python3 -m experiments.masking_curve run --pairs relaxed    # secondary: maskers with unannotated target label
Each mode's results replace that mode's block ("strict" / "relaxed") in outputs/drum-masking/masking_curve.json.
extract exit codes: 2 archive absent, 3 download unfinished, 4 unsafe archive or destination, 5 corrupt archive or
checksum mismatch, 6 requested clips missing from the archive.

Head and features: the shipped MLP loaded through InstrumentModel.load (outputs/openmic-mlp.pt, stored validation
thresholds), VGGish features from ohrin.vggish exactly as the app builds them (uint8 PCA-quantised embeddings, mean
of up to ten per ~10 s block, /255, sigmoid, mean over blocks). The OpenMIC stored features are the same scale
(uint8 mean / 255), so no rescaling is applied; experiments/validate_vggish.py measures how close they are.

Pre-registration note: the strict/relaxed and neutral definitions below were chosen from label counts on the test
split before any model outcome was computed. The decision rule applies per class (drums, bass); there is no pooling
across classes. Failing to reach "continue" means insufficient evidence, not equivalence.

Pairs (per target class, official split01 test only, seed fixed below; clips that are target candidates for either
class are excluded from every masker and neutral pool):
  target   target-class label annotated positive (Y_mask, Y_true >= 0.5)
  masker   strict: target-class label annotated absent (Y_true <= 0.2); relaxed (secondary): annotated absent OR not
           annotated, so the masker may contain the target class. In both, guitar or bass annotated positive
           (Y_true >= 0.5); for the bass target the masker is therefore a guitar clip.
  neutral  guitar and bass both annotated absent (Y_true <= 0.2) AND the target class annotated absent. Only
           MIN_NEUTRAL_CLIPS or more such clips make the neutral-clip arm available; otherwise it is "unavailable"
           and the verdict is provisional (pink-noise control only). Pairs do not need a neutral clip.
Each masker or neutral clip is reused at most three times, never from the target's source track. Maskers and neutrals
are assigned only to targets that are kept after truncation to N_PAIRS.

Mixing: 16 kHz mono. The masker-to-target ratio (-6/0/+6/+12/+18 dB) is defined on RMS measured inside 125-7500 Hz
(VGGish's mel range) for ALL signals, real clips and noise alike. The full-band RMS ratio is reported as secondary
information. The scaled masker is summed with the unfiltered target and the sum is peak-normalised to -1 dBFS.
Pink noise is band-limited to the same 125-7500 Hz range. Peak-normalised audio is used for every curve condition
(unmixed, mixed, masker alone); parity uses the raw decoded clips, and the raw unmixed recall is reported beside the
stored-feature recall and the peak-normalised baseline.

Gate 0 (per class): parity evidence must exist (audio_subset present, >= 200 distinct clips) or the verdict is
inconclusive_parity. Tolerance (<= 0.05, same decisions) is judged on drums and bass only; a failure is reported as a gap
and the run continues on the re-extracted baseline, provisional when target decision flips exceed 5% of kept targets.
guitar/voice rows are information only. Then stop if unmixed or real-masker 0 dB recall < 0.5.
"continue" needs, against EACH available informative control separately (pink noise; target-absent neutral clips), an
excess recall drop (0 to +12 dB, real minus control) >= GATE_MARGIN with the 95% crossed-bootstrap CI above 0. If only one
control passes the verdict is controls_disagree; a pass against pink noise alone is pink_only_excess and never states the
neutral-masker conclusion. The averaged composite is information only. A control arm whose 0 dB recall is <= 0.02 or
>= 0.98 is flagged saturated. Only a floor arm (<= 0.02 at 0 dB) is excluded and reports passes: null: it cannot show a
drop. A ceiling arm can, so it stays informative and can fail or veto. An arm needs >= MIN_DISTINCT_SOURCES distinct target
tracks and masker source tracks, else the verdict is too_few_sources. The pink arm is mixed PINK_OFFSET_DB below the
nominal ratio, so its excess compares the real 0 to +12 dB drop with a pink drop over -12 to 0 dB; this favours finding
an excess and every pink-only statement says so.
CIs resample target source tracks and masker (or neutral) clips jointly (crossed bootstrap, weights = product of the
resampling counts). Leave-one-masker-out and leave-one-neutral-out ranges are reported as sensitivity. The only
positive conclusion the output may state is "degradation exceeds the neutral-masker control".
"""

import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import tarfile
import tempfile
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from scripts.fetch_openmic import ARCHIVE_MD5



def _pooling():
    """experiments.pooling_test imports torch, so it is loaded on first use: `--help` and `extract` need no torch."""
    from experiments import pooling_test
    return pooling_test


def _instruments():
    from ohrin import instruments
    return instruments


def __getattr__(name):  # lazy re-exports (PEP 562)
    if name in ("percentile_ci", "to_jsonable"):
        return getattr(_pooling(), name)
    if name in ("TARGETS", "load_openmic"):
        return getattr(_instruments(), name)
    raise AttributeError(name)


SEED = 42
PAIR_MODES = ("strict", "relaxed")
N_PAIRS = 300
MAX_REUSE = 3
MIN_NEUTRAL_CLIPS = 5
MIN_DISTINCT_SOURCES = 5
RATIOS = (-6, 0, 6, 12, 18)
BAND_HZ = (125.0, 7500.0)
PEAK_DBFS = -1.0
SAMPLE_RATE = 16000
CLASSES = ("drums", "bass")
MASKER_INSTRUMENTS = ("guitar", "bass")
TARGET_MIN, MASKER_ABSENT_MAX, MASKER_PRESENT_MIN = 0.5, 0.2, 0.5
GATE_BASELINE_MIN = 0.5
GATE_MARGIN = 0.10
SATURATION = 0.02
SILENT_RMS = 1e-9
PARITY_TOL = 0.05
PROVISIONAL_FLIP_FRACTION = 0.05
PINK_OFFSET_DB = -12.0  # pre-registered: pink is mixed this much lower than the nominal ratio
NOISE_DRAWS = 3
PARITY_MIN_CLIPS = 200
MAX_MEMBER_BYTES = 5 * 1024 * 1024
ARCHIVE = Path("cache/openmic-2018-v1.0.0.tgz")
MEMBER_RE = re.compile(r"^(?:openmic-2018(?:-v1\.0\.0)?/)?audio/(\d{3})/(\d{6}_\d+)\.ogg$")
RELAXED_CAVEAT = ("unannotated maskers may contain the target class; false positives and the drop may be contaminated")
CONCLUSION = "degradation exceeds the neutral-masker control"
POOLING_NOTE = "not evaluated here (0-pre(ii) pooling test is separate)"
NOTES = ("Full-mix targets, so masker-to-target ratios are approximate (defined on 125-7500 Hz RMS). Synthetic sums of "
         "two OpenMIC excerpts, not mastered records. The conclusion is limited to degradation versus the neutral-masker "
         "controls; it does not show masking as the cause. CIs are crossed bootstraps over target source tracks and "
         "masker/neutral clips; pink noise is mixed 12 dB below the nominal ratio (pre-registered), so its excess compares the real 0 to +12 dB drop with a pink drop over -12 to 0 dB, which favours finding an excess; it is averaged over 3 independent noise draws per pair (their variance enters the bootstrap through the per-pair mean; per-draw recall is reported separately); the head and the seed are fixed. Thresholds are the stored "
         "validation thresholds of the shipped head. Failing to continue means insufficient evidence, not equivalence.")


# ---------------------------------------------------------------- pair builder

@dataclass(frozen=True)
class Pair:
    target: str
    masker: str
    neutral: Optional[str] = None


def track_of(key):
    return str(key).split("_")[0]


def _assign(targets, candidates, rng, max_reuse, limit=None):
    """Give targets one candidate each from a different source track, least-used first, each at most max_reuse times.

    Stops after `limit` assignments. Returns {target: candidate} in target order.
    """
    uses = {c: 0 for c in candidates}
    chosen = {}
    for t in targets:
        if limit is not None and len(chosen) >= limit:
            break
        eligible = [c for c in candidates if uses[c] < max_reuse and track_of(c) != track_of(t)]
        if not eligible:
            continue
        fewest = min(uses[c] for c in eligible)
        pool = [c for c in eligible if uses[c] == fewest]
        pick = pool[int(rng.integers(len(pool)))]
        uses[pick] += 1
        chosen[t] = pick
    return chosen


def target_candidates(keys, true, mask, test_indices, target):
    """Keys of test clips annotated positive for the target class."""
    col = _instruments().TARGETS.index(target)
    keys = np.asarray(keys).astype(str)
    return {keys[r] for r in np.asarray(test_indices) if bool(mask[r, col]) and true[r, col] >= TARGET_MIN}


def build_pairs(keys, true, mask, test_indices, target, n_pairs=N_PAIRS, seed=SEED, max_reuse=MAX_REUSE,
                mode="strict", exclude=frozenset()):
    """Select target / masker / optional neutral triples for one class from the test split.

    keys (n,), true and mask (n, 4) in TARGETS order, test_indices into keys. Returns (pairs, availability).
    Only test_indices rows are ever considered, so no pair can cross partitions. `exclude` (clip keys) is removed from
    the masker and neutral pools, as are this class's own target candidates.
    mode "strict" (primary) needs the masker's target label annotated absent; "relaxed" (secondary) also accepts an
    unannotated target label, so such maskers may contain the target class.
    """
    if mode not in PAIR_MODES:
        raise ValueError(f"mode must be one of {PAIR_MODES}")
    keys = np.asarray(keys).astype(str)
    col = {name: i for i, name in enumerate(_instruments().TARGETS)}
    t = col[target]
    rows = np.asarray(test_indices)
    order = rows[np.argsort(keys[rows], kind="stable")]  # sorted by key: independent of file order

    def annotated(row, name, present):
        c = col[name]
        return bool(mask[row, c]) and (true[row, c] >= MASKER_PRESENT_MIN if present else true[row, c] <= MASKER_ABSENT_MAX)

    targets = [r for r in order if bool(mask[r, t]) and true[r, t] >= TARGET_MIN]
    excluded = set(exclude) | {keys[r] for r in targets}
    free = [r for r in order if keys[r] not in excluded]
    maskers = [r for r in free
               if (annotated(r, target, False) or (mode == "relaxed" and not mask[r, t]))
               and any(annotated(r, m, True) for m in MASKER_INSTRUMENTS)]
    neutral_any = [r for r in free if all(annotated(r, m, False) for m in MASKER_INSTRUMENTS)]
    neutral_pool = [r for r in neutral_any if annotated(r, target, False)]
    rng = np.random.default_rng(seed)
    target_keys = [keys[targets[i]] for i in rng.permutation(len(targets))]
    real = _assign(target_keys, [keys[r] for r in maskers], rng, max_reuse, limit=n_pairs)
    neutral = {}
    if len(neutral_pool) >= MIN_NEUTRAL_CLIPS:
        neutral = _assign(list(real), [keys[r] for r in neutral_pool], rng, max_reuse)
    pairs = [Pair(k, m, neutral.get(k)) for k, m in real.items()]
    available = {"target": target, "targets_available": len(targets), "real_maskers_available": len(maskers),
                 "neutral_clips_available": len(neutral_any), "neutral_target_absent_available": len(neutral_pool),
                 "neutral_arm": "available" if neutral else "unavailable",
                 "pairs_requested": n_pairs, "pairs_built": len(pairs), "pairs_with_neutral": len(neutral),
                 "max_reuse": max_reuse, "seed": seed, "mode": mode}
    return pairs, available


def pair_keys(pairs):
    return sorted({k for p in pairs for k in (p.target, p.masker, p.neutral) if k})


# ---------------------------------------------------------------- safe extraction

class UnsafeArchive(ValueError):
    pass


class DownloadUnfinished(RuntimeError):
    pass


class ArchiveCorrupt(Exception):
    pass


def archive_member_key(member, wanted):
    """Key to extract for a tar member, or None to skip it. Raises UnsafeArchive for hostile members."""
    name = member.name
    parts = name.split("/")
    if name.startswith("/") or "\\" in name or "\x00" in name or ".." in parts:
        raise UnsafeArchive(f"unsafe member name: {name!r}")
    if member.issym() or member.islnk() or member.isdev() or member.isfifo():
        raise UnsafeArchive(f"link or device member: {name!r}")
    if member.isdir():
        return None
    if not member.isfile():
        raise UnsafeArchive(f"unsupported member type: {name!r}")
    match = MEMBER_RE.match(name[2:] if name.startswith("./") else name)
    if not match or match.group(2)[:3] != match.group(1) or match.group(2) not in wanted:
        return None
    if member.size > MAX_MEMBER_BYTES:
        raise UnsafeArchive(f"member over {MAX_MEMBER_BYTES} bytes: {name!r}")
    return match.group(2)


def audio_path(audio_root, key):
    return Path(audio_root) / key[:3] / f"{key}.ogg"


def file_md5(path):
    digest = hashlib.md5()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def check_destination(audio_root, keys):
    """Refuse symlinked destination components, symlinked clip directories and non-regular existing targets."""
    path = Path(os.path.abspath(audio_root))
    for component in (path, *path.parents):
        if component.is_symlink():
            raise UnsafeArchive(f"destination component is a symlink: {component}")
    for key in keys:
        folder, dest = path / key[:3], audio_path(path, key)
        if folder.is_symlink():
            raise UnsafeArchive(f"clip directory is a symlink: {folder}")
        if os.path.lexists(dest) and (dest.is_symlink() or not dest.is_file()):
            raise UnsafeArchive(f"existing target is not a regular file: {dest}")


def extract_clips(archive, audio_root, keys, expected_md5=ARCHIVE_MD5):
    """One streaming pass over the archive, writing only the wanted OGG clips under audio_root/<NNN>/<key>.ogg.

    Output names come from the validated key, never from the member path. The archive's MD5 is checked first (one
    extra sequential read) and only when something must be extracted. The stream stops as soon as every missing key
    is found, so members after the last wanted one are never inspected. Returns
    {"extracted": n, "already_present": n, "missing": [keys]}. Raises DownloadUnfinished while a .part file exists,
    ArchiveCorrupt on checksum or read errors (partial .tmp files are removed), UnsafeArchive for hostile input.
    """
    archive, audio_root = Path(archive), Path(audio_root)
    if Path(str(archive) + ".part").exists():
        raise DownloadUnfinished(f"{archive}.part exists: the download is not finished; not reading the archive.")
    keys = set(keys)
    check_destination(audio_root, keys)
    todo = {k for k in keys if not audio_path(audio_root, k).is_file()}
    summary = {"extracted": 0, "already_present": len(keys) - len(todo), "missing": []}
    if not todo:
        return summary
    if not archive.is_file():
        raise FileNotFoundError(archive)
    digest = file_md5(archive)
    if digest != expected_md5:
        raise ArchiveCorrupt(f"archive checksum mismatch: expected MD5 {expected_md5}, got {digest}")
    remaining, tmp = set(todo), None
    try:
        with tarfile.open(archive, mode="r|gz") as tar:  # stream: a single sequential read
            for member in tar:
                key = archive_member_key(member, remaining)
                if key is None:
                    continue
                dest = audio_path(audio_root, key)
                dest.parent.mkdir(parents=True, exist_ok=True)
                handle, name = tempfile.mkstemp(prefix=dest.name + ".", suffix=".tmp", dir=dest.parent)  # O_EXCL, 0600
                tmp = Path(name)
                source, written = tar.extractfile(member), 0
                with os.fdopen(handle, "wb") as out:
                    while chunk := source.read(1 << 16):
                        written += len(chunk)
                        if written > member.size:
                            break
                        out.write(chunk)
                with tmp.open("rb") as written_file:
                    head = written_file.read(4)
                if written != member.size or head != b"OggS":
                    raise UnsafeArchive(f"bad clip content for {key}")
                os.chmod(tmp, 0o644)
                os.replace(tmp, dest)
                tmp = None
                remaining.discard(key)
                summary["extracted"] += 1
                if not remaining:
                    break
    except (tarfile.TarError, EOFError, gzip.BadGzipFile, zlib.error, OSError) as exc:
        raise ArchiveCorrupt(f"archive could not be read ({type(exc).__name__}: {exc})") from exc
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    summary["missing"] = sorted(remaining)
    return summary


# ---------------------------------------------------------------- mixing

def rms(x):
    return float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))


def band_mask(n):
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    return (freqs >= BAND_HZ[0]) & (freqs <= BAND_HZ[1])


def band_rms(x):
    """RMS of the part of x inside BAND_HZ, from the rfft (Parseval; DC and Nyquist are outside the band)."""
    x = np.asarray(x, dtype=np.float64)
    spectrum = np.fft.rfft(x)
    return float(np.sqrt(2.0 * np.sum(np.abs(spectrum[band_mask(len(x))]) ** 2)) / len(x))


def band_filter(x):
    """x with everything outside BAND_HZ removed (time-domain; independent route to the band RMS)."""
    x = np.asarray(x, dtype=np.float64)
    spectrum = np.fft.rfft(x)
    spectrum[~band_mask(len(x))] = 0.0
    return np.fft.irfft(spectrum, len(x))


def peak_normalise(x, dbfs=PEAK_DBFS):
    peak = float(np.max(np.abs(x)))
    if peak == 0.0:
        raise ValueError("Cannot peak-normalise silence.")
    return x * (10 ** (dbfs / 20) / peak)


def mix_at_ratio(target, masker, ratio_db):
    """Sum target and a masker scaled to ratio_db (masker over target, 125-7500 Hz RMS), peak-normalised to -1 dBFS.

    Returns (mix, achieved_band_db, achieved_fullband_db). Both achieved ratios are measured on the final components
    (scaled target and scaled masker after normalisation), the band one by filtering rather than by the spectral sum
    used to scale. The full-band figure is secondary information.
    """
    n = min(len(target), len(masker))
    target, masker = np.asarray(target[:n], dtype=np.float64), np.asarray(masker[:n], dtype=np.float64)
    band_t, band_m = band_rms(target), band_rms(masker)
    if band_t < SILENT_RMS or band_m < SILENT_RMS:
        raise ValueError("Silent target or masker inside the analysis band.")
    scaled = masker * (band_t * 10 ** (ratio_db / 20) / band_m)
    total = target + scaled
    gain = 10 ** (PEAK_DBFS / 20) / float(np.max(np.abs(total)))
    target_part, masker_part = target * gain, scaled * gain
    achieved = 20 * np.log10(rms(band_filter(masker_part)) / rms(band_filter(target_part)))
    fullband = 20 * np.log10(rms(masker_part) / rms(target_part))
    return total * gain, float(achieved), float(fullband)


def pink_noise(n, rng):
    """Unit-RMS noise with a 1/f power spectrum, limited to BAND_HZ."""
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1.0 / SAMPLE_RATE)
    inside = band_mask(n)
    spectrum[~inside] = 0.0
    spectrum[inside] /= np.sqrt(freqs[inside])
    noise = np.fft.irfft(spectrum, n)
    return noise / rms(noise)


# ---------------------------------------------------------------- scoring and parity

def score_samples(model, samples):
    """Per-class probabilities for audio, built exactly as InstrumentModel.predict does after decoding."""
    from ohrin.instrument_inference import MIN_SEGMENT_EXAMPLES, SEGMENT_EXAMPLES
    embeddings = model.embed(samples)
    if len(embeddings) == 0:
        raise ValueError("The clip is too short for instrument analysis.")
    groups = [embeddings[i:i + SEGMENT_EXAMPLES] for i in range(0, len(embeddings), SEGMENT_EXAMPLES)]
    groups = [g for g in groups if len(g) >= MIN_SEGMENT_EXAMPLES] or [embeddings]
    return model._probabilities(np.stack([g.astype(float).mean(axis=0) for g in groups])).mean(axis=0)


def stored_probabilities(model, features):
    """Head probabilities from OpenMIC's stored features (uint8 mean / 255, as load_openmic builds them)."""
    import torch
    with torch.no_grad():
        return torch.sigmoid(model.head(torch.as_tensor(features, dtype=torch.float32))).numpy()


def parity_rows(keys, stored, fresh, thresholds, names):
    """Compare stored-feature and re-extracted head probabilities for ALL classes (names, one per column)."""
    stored, fresh = np.asarray(stored), np.asarray(fresh)
    if stored.shape != fresh.shape or stored.shape[1] != len(names) or len(keys) != len(stored):
        raise ValueError("parity inputs disagree in shape")
    rows = []
    for i, key in enumerate(keys):
        for c, name in enumerate(names):
            diff = abs(float(stored[i, c]) - float(fresh[i, c]))
            same = bool(stored[i, c] >= thresholds[c]) == bool(fresh[i, c] >= thresholds[c])
            rows.append({"key": key, "class": name, "stored": float(stored[i, c]), "reextracted": float(fresh[i, c]),
                         "abs_diff": diff, "threshold": float(thresholds[c]), "same_decision": same,
                         "pass": bool(diff <= PARITY_TOL and same)})
    return rows


def parity_summary(rows, classes=None):
    """Summary over rows, optionally restricted to the named classes (the rest stay information only)."""
    if classes is not None:
        rows = [r for r in rows if r["class"] in classes]
    return {"n_clips": len({r["key"] for r in rows}), "max_abs_diff": max((r["abs_diff"] for r in rows), default=None),
            "decision_disagreements": sum(not r["same_decision"] for r in rows),
            "rows_failing": sum(not r["pass"] for r in rows), "pass": bool(rows) and all(r["pass"] for r in rows)}


def parity_gate(subset_summary, clip_summary, min_clips=PARITY_MIN_CLIPS):
    """Whether parity evidence exists. Returns {"ok", "reasons", "gaps"}.

    Missing evidence blocks a verdict. A tolerance failure on the target classes does not: the run continues on the
    re-extracted baseline, the gap is reported, and per-class provisional status follows from decision flips."""
    reasons, gaps = [], []
    if subset_summary is None:
        reasons.append("audio_subset missing")
    elif not subset_summary["pass"]:
        gaps.append("audio_subset parity outside tolerance")
    if clip_summary is None:
        reasons.append("test-clip parity not run")
    else:
        if not clip_summary["pass"]:
            gaps.append("test-clip parity outside tolerance")
        if clip_summary["n_clips"] < min_clips:
            reasons.append(f"fewer than {min_clips} distinct clips checked")
    return {"ok": not reasons, "reasons": reasons, "gaps": gaps}


def format_parity(rows):
    lines = [f"{'key':<16}{'class':<8}{'stored':>8}{'re-extr.':>10}{'|diff|':>8}{'thr':>6}  decision  pass"]
    for r in rows:
        lines.append(f"{r['key']:<16}{r['class']:<8}{r['stored']:>8.3f}{r['reextracted']:>10.3f}{r['abs_diff']:>8.3f}"
                     f"{r['threshold']:>6.2f}  {'same' if r['same_decision'] else 'DIFFERS':<8}  {'ok' if r['pass'] else 'FAIL'}")
    return "\n".join(lines)


def subset_parity(model, data, data_root, decode):
    """0-pre(i) on the 8-clip audio_subset: stored features versus features re-extracted from the OGG files."""
    index = {k: i for i, k in enumerate(data["keys"])}
    paths = sorted((Path(data_root) / "audio_subset").rglob("*.ogg"))
    if not paths:
        return None
    keys = [p.stem for p in paths]
    stored = stored_probabilities(model, data["features"][[index[k] for k in keys]])
    fresh = np.stack([score_samples(model, decode(p)) for p in paths])
    return keys, stored, fresh


def parity_clip_keys(data, n_total=PARITY_MIN_CLIPS, seed=SEED):
    """Clips for the >= 200-clip parity check: every clip either pair mode uses, topped up from the test split."""
    used = set()
    for mode in PAIR_MODES:
        for pairs, _ in all_pairs(data, mode).values():
            used.update(pair_keys(pairs))
    keys = np.asarray(data["keys"]).astype(str)
    spare = sorted(set(keys[np.asarray(data["test_indices"])]) - used)
    rng = np.random.default_rng(seed + 1)
    extra = [spare[i] for i in rng.permutation(len(spare))[:max(0, n_total - len(used))]]
    return sorted(used | set(extra))


def clip_parity(model, data, keys, load_raw):
    """Stored versus re-extracted head output on raw (not level-normalised) decoded clips, all four classes."""
    index = {k: i for i, k in enumerate(data["keys"])}
    stored = stored_probabilities(model, data["features"][[index[k] for k in keys]])
    fresh = np.stack([score_samples(model, load_raw(k)) for k in keys])
    return parity_rows(keys, stored, fresh, model.thresholds, list(model.classes)), dict(zip(keys, fresh)), dict(zip(keys, stored))


# ---------------------------------------------------------------- bootstrap and statistics

def bootstrap_weights(factors, n_boot, seed=0):
    """Crossed-bootstrap weights (n_boot, n): per factor, resample its distinct ids with replacement; weight = product."""
    rng = np.random.default_rng(seed)
    n = len(factors[0])
    weights = np.ones((n_boot, n))
    for factor in factors:
        unique, inverse = np.unique(np.asarray(factor).astype(str), return_inverse=True)
        counts = rng.multinomial(len(unique), np.full(len(unique), 1.0 / len(unique)), size=n_boot)
        weights *= counts[:, inverse]
    return weights


def weighted_mean(values, weights):
    total = weights.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(total > 0, (weights * values).sum(axis=1) / total, np.nan)


def stat(values, factors, n_boot=1000, seed=0):
    """Mean over non-NaN pairs with a crossed-bootstrap 95% CI. factors: id arrays aligned with values."""
    values = np.asarray(values, dtype=float)
    ok = ~np.isnan(values)
    if not ok.any():
        return {"value": float("nan"), "ci95": [float("nan"), float("nan")]}
    weights = bootstrap_weights([np.asarray(f)[ok] for f in factors], n_boot, seed)
    return {"value": float(values[ok].mean()), "ci95": _pooling().percentile_ci(weighted_mean(values[ok], weights))}


def leave_one_out(values, ids):
    """Mean of values without each distinct id in turn: range and the ids that move it most."""
    values, ids = np.asarray(values, dtype=float), np.asarray(ids).astype(str)
    ok = ~np.isnan(values)
    results = {}
    for i in np.unique(ids[ok]):
        keep = ok & (ids != i)
        if keep.any():
            results[i] = float(values[keep].mean())
    if not results:
        return {"n": 0}
    low, high = min(results, key=results.get), max(results, key=results.get)
    return {"n": len(results), "min": results[low], "min_without": low, "max": results[high], "max_without": high,
            "all_at_least_margin": bool(min(results.values()) >= GATE_MARGIN)}


def auc(positive, negative):
    """P(positive score > negative score) over all pairs, ties half; NaN-free inputs."""
    positive, negative = np.asarray(positive, dtype=float), np.asarray(negative, dtype=float)
    positive, negative = positive[~np.isnan(positive)], negative[~np.isnan(negative)]
    if not len(positive) or not len(negative):
        return float("nan")
    greater = (positive[:, None] > negative[None, :]).mean()
    ties = (positive[:, None] == negative[None, :]).mean()
    return float(greater + .5 * ties)


def hits_from_scores(scores, threshold):
    """Detections (score >= threshold) as 0/1 arrays with the same layout; NaN (missing neutral) stays NaN."""
    def conv(v):
        v = np.asarray(v, dtype=float)
        return np.where(np.isnan(v), np.nan, (v >= threshold).astype(float))
    return {k: ({r: conv(x) for r, x in val.items()} if isinstance(val, dict) else conv(val)) for k, val in scores.items()}


def ids_from_pairs(pairs):
    return {"target": np.array([p.target for p in pairs]), "masker": np.array([p.masker for p in pairs]),
            "neutral": np.array([p.neutral or "" for p in pairs])}


def _drop(arrays, low=0, high=12):
    return arrays[low] - arrays[high]


def control_arm(name, real_drop, control_drop, factors, ctrl_0db, n_boot):
    """Excess drop (real minus control, per pair) against one control, with sensitivity and saturation flags."""
    excess = real_drop - control_drop
    valid = ~np.isnan(excess)
    out = {"control": name, "available": bool(valid.any()), "n_pairs": int(valid.sum())}
    if not valid.any():
        return out
    saturation = ("saturated_low" if ctrl_0db <= SATURATION else "saturated_high" if ctrl_0db >= 1 - SATURATION else "ok")
    est = stat(excess, factors, n_boot)
    out.update({"excess_drop": est, "control_recall_0db": float(ctrl_0db), "saturation": saturation,
                "flag": "control saturated" if saturation != "ok" else None,
                "informative": saturation != "saturated_low",
                "passes": (bool(est["value"] >= GATE_MARGIN and est["ci95"][0] > 0) if saturation != "saturated_low" else None)})
    out["leave_one_out"] = {"masker": leave_one_out(excess, factors[1])}
    if len(factors) > 2:
        out["leave_one_out"]["neutral"] = leave_one_out(excess, factors[2])
    return out


GATE_RULE = (f"parity evidence must exist (tolerance gaps on drums/bass are reported and make the verdict provisional when "
             f"more than {PROVISIONAL_FLIP_FRACTION:.0%} of distinct targets flip or flips are unmeasured); stop if unmixed "
             f"or real-masker 0 dB recall < {GATE_BASELINE_MIN}; need >= {MIN_DISTINCT_SOURCES} distinct target tracks and "
             f"masker source tracks; continue only if the excess drop 0 to +12 dB is >= {GATE_MARGIN} with 95% "
             "crossed-bootstrap CI (tracks) above 0 against every informative control; the pink control is mixed "
             f"{-PINK_OFFSET_DB:.0f} dB below nominal, so its drop is taken over -12 to 0 dB actual SNR")


def gate0(hits, ids, parity, n_boot=1000, target_flips=None):
    """Gate 0 on per-pair 0/1 hit arrays; see the module docstring for the rule."""
    tr = np.array([track_of(k) for k in ids["target"]])
    if not len(tr):
        return {"verdict": "no_pairs", "conclusion": "Not established: no usable pairs", "provisional": False,
                "provisional_reason": None, "parity": parity, "target_flips": target_flips, "arms": {},
                "composite_info_only": None, "pooling_note": POOLING_NOTE, "rule": GATE_RULE}
    mk = np.array([track_of(k) for k in ids["masker"]])  # resample by source track: clips of one track are correlated
    nu = np.array([track_of(k) if k else "" for k in ids["neutral"]])
    real_drop = _drop(hits["real"])
    unmixed, real0 = float(hits["unmixed"].mean()), float(hits["real"][0].mean())
    thin = len(set(tr)) < MIN_DISTINCT_SOURCES or len(set(mk)) < MIN_DISTINCT_SOURCES
    crop = [nu] if (nu != "").any() else []  # neutral identity also sets the common crop of the real and pink arms
    pink = control_arm("pink_noise", real_drop, _drop(hits["pink"]), [tr, mk, *crop], float(hits["pink"][0].mean()), n_boot)
    if pink.get("available"):
        # Sensitivity: the real arm over 0 to +6 dB against the pink arm over the same actual SNR (nominal +12 to +18
        # dB, since pink is mixed PINK_OFFSET_DB lower). Reported, never used by the verdict.
        matched = (hits["real"][0] - hits["real"][6]) - (hits["pink"][12] - hits["pink"][18])
        pink["sensitivity_matched_actual_snr_0_to_6"] = stat(matched, [tr, mk, *crop], n_boot)
    neutral_valid = ~np.isnan(hits["neutral_clip"][0])
    if neutral_valid.sum() and len(set(nu[neutral_valid])) >= MIN_NEUTRAL_CLIPS:
        clip_drop = _drop(hits["neutral_clip"])
        clip = control_arm("target_absent_neutral_clips", real_drop, clip_drop, [tr, mk, nu],
                           float(np.nanmean(hits["neutral_clip"][0])), n_boot)
        composite = real_drop - (_drop(hits["pink"]) + clip_drop) / 2
        composite_info = stat(composite, [tr, mk, nu], n_boot)
    else:
        clip = {"control": "target_absent_neutral_clips", "available": False, "status": "unavailable",
                "n_pairs": int(neutral_valid.sum())}
        composite_info = None
    arms = {"pink_noise": pink, "neutral_clips": clip}
    usable = {k: a for k, a in arms.items() if a.get("available") and a.get("informative")}
    names = {"pink_noise": "pink-noise", "neutral_clips": "neutral-clip"}
    if not parity["ok"]:
        verdict = "inconclusive_parity"
        conclusion = f"Not established: feature parity not met ({'; '.join(parity['reasons'])}); parity unestablished"
    elif not (unmixed >= GATE_BASELINE_MIN and real0 >= GATE_BASELINE_MIN):
        verdict, conclusion = "stop_baseline", "Not established: baseline recall below 0.5; the masking comparison is not interpretable"
    elif thin:
        verdict = "too_few_sources"
        conclusion = (f"Not established: fewer than {MIN_DISTINCT_SOURCES} distinct target tracks or masker source tracks; "
                      "the confidence intervals are not meaningful")
    elif not usable:
        verdict, conclusion = "no_informative_control", "Not established: no informative control arm"
    else:
        passing = [k for k, a in usable.items() if a["passes"]]
        if len(passing) == len(usable) == 1 and "pink_noise" in usable:
            verdict = "pink_only_excess"
            conclusion = ("Shown against the pink-noise control only: degradation exceeds a pink-noise control mixed "
                          f"{-PINK_OFFSET_DB:.0f} dB below the nominal ratio (a weaker control); no informative "
                          "neutral-clip control, so the neutral-masker claim is not made")
        elif len(passing) == len(usable):
            verdict, conclusion = "continue", CONCLUSION
        elif not passing:
            verdict = "stop_no_excess"
            conclusion = "Not shown: an excess drop at the gate margin over every control; insufficient evidence, not equivalence"
        else:
            failing = [names[k] for k in usable if k not in passing]
            verdict = "controls_disagree"
            conclusion = (f"Not established: controls disagree ({names[passing[0]]} shows an excess, "
                          f"{', '.join(failing)} does not); insufficient evidence")
    reasons = []
    if parity["ok"] and len(usable) < 2 and usable:
        reasons.append("pink-noise control only" if "pink_noise" in usable else "neutral-clip control only")
    flips_over = bool(target_flips and target_flips["flips"] > PROVISIONAL_FLIP_FRACTION * target_flips["n"])
    if parity["ok"] and parity.get("gaps") and target_flips is None:
        reasons.append("parity gap and decision flips not measured")
    if parity["ok"] and flips_over:
        reasons.append(f"{target_flips['flips']} of {target_flips['n']} distinct targets flip decision between stored "
                       "and re-extracted features")
    provisional = bool(reasons)
    reason = f"provisional ({'; '.join(reasons)})" if provisional else None
    return {"verdict": verdict, "conclusion": conclusion, "provisional": provisional, "provisional_reason": reason,
            "parity": parity, "target_flips": target_flips, "unmixed_recall": unmixed, "real_0db_recall": real0,
            "arms": arms, "composite_info_only": composite_info, "pooling_note": POOLING_NOTE,
            "rule": GATE_RULE}


def mean_per_id(values, id_array):
    """(means, ids): mean of the non-NaN values of each non-empty id, in sorted id order (independent of pair order)."""
    groups = {}
    for v, i in zip(values, id_array):
        if i and not np.isnan(v):
            groups.setdefault(i, []).append(float(v))
    names = sorted(groups)
    return np.array([float(np.mean(groups[n])) for n in names]), np.array(names)


def summarise(scores, ids, threshold, parity, n_boot=1000, target_flips=None):
    """Recall, mean probability, AUC, masker-alone rates and Gate 0 for one class under one pair mode."""
    hits = hits_from_scores(scores, threshold)
    pink_draws = None
    if np.ndim(scores["alone_pink"]) == 2:  # (pairs, NOISE_DRAWS): hits average over draws, scores average too
        pink_draws = {r: [float(v) for v in np.nanmean(hits["pink"][r], axis=0)] for r in hits["pink"]}
        hits = {**hits, "pink": {r: np.nanmean(v, axis=1) for r, v in hits["pink"].items()},
                "alone_pink": np.nanmean(hits["alone_pink"], axis=1)}
        scores = {**scores, "pink": {r: np.nanmean(v, axis=1) for r, v in scores["pink"].items()},
                  "alone_pink": np.nanmean(scores["alone_pink"], axis=1)}
    tr = np.array([track_of(k) for k in ids["target"]])
    mk, nu = ids["masker"], ids["neutral"]
    mk_t = np.array([track_of(k) for k in mk])  # bootstrap units are source tracks, not clips
    nu_t = np.array([track_of(k) if k else "" for k in nu])
    # The common crop depends on every clip of a pair, neutral included, so when a neutral arm exists its clip identity
    # is also a bootstrap factor of the real and pink arms (conservative: wider intervals).
    crop = [nu_t] if (nu_t != "").any() else []
    factors = {"real": [tr, mk_t, *crop], "pink": [tr, mk_t, *crop], "neutral_clip": [tr, nu_t]}

    def curve(source):
        out = {"unmixed": stat(source["unmixed"], [tr, mk_t, *crop], n_boot)}
        for kind, fs in factors.items():
            out[kind] = {r: stat(v, fs, n_boot) for r, v in source[kind].items()}
        return out

    def first_per_id(values, id_array):
        """Per distinct clip, the mean over its crop outcomes (a clip is cropped differently in different pairs)."""
        return mean_per_id(values, id_array)

    rates = {}
    for kind, alone, id_array in (("real", hits["alone_real"], mk), ("neutral_clip", hits["alone_neutral_clip"], nu)):
        vals, vids = first_per_id(alone, id_array)
        rates[kind] = {**(stat(vals, [vids], n_boot) if len(vals) else {"value": float("nan"), "ci95": [float("nan")] * 2}),
                       "n_distinct_clips": int(len(vals))}
    rates["pink"] = {**stat(hits["alone_pink"], [tr, mk_t, *crop], n_boot), "n_distinct_clips": int(len(tr))}
    alone_scores = {"real": scores["alone_real"], "pink": scores["alone_pink"], "neutral_clip": scores["alone_neutral_clip"]}
    mean_drop = {k: _drop(scores[k]) for k in factors}
    continuous = {
        "mean_probability_drop_0_to_12": {k: stat(v, factors[k], n_boot) for k, v in mean_drop.items()},
        "excess_mean_probability_drop_vs_pink": stat(mean_drop["real"] - mean_drop["pink"], [tr, mk_t, *crop], n_boot),
        "excess_mean_probability_drop_vs_neutral_clip": stat(mean_drop["real"] - mean_drop["neutral_clip"], [tr, mk_t, nu_t], n_boot),
        "auc_mixed_vs_masker_alone": {k: {r: auc(scores[k][r], alone_scores[k]) for r in scores[k]} for k in factors},
        "note": "secondary: mean target-class probability, and the chance a mixed clip outscores the masker alone; the pink "
                "values use the mean of the noise draws"}
    n_neutral = int(len(set(nu[nu != ""])))
    return {"n_pairs": int(len(tr)), "distinct_targets": int(len(set(ids["target"]))), "distinct_target_tracks": int(len(set(tr))),
            "distinct_maskers": int(len(set(mk))), "distinct_masker_tracks": int(len({track_of(k) for k in mk})),
            "distinct_neutrals": n_neutral, "under_powered_vs_n": bool(len(tr) < N_PAIRS), "pink_noise_draw_recall": pink_draws,
            "threshold": float(threshold),
            "recall": curve(hits), "mean_probability": curve(scores), "continuous": continuous,
            "masker_alone_rate": rates, "gate0": gate0(hits, ids, parity, n_boot, target_flips)}


# ---------------------------------------------------------------- collecting scores

def collect_scores(pairs, col, load, score, ratios=RATIOS, seed=SEED, cache=None, progress=None):
    """Target-class probability for every condition. load(key) -> mono 16 kHz samples; score(samples) -> (4,) probs.

    Every condition of a pair (unmixed, masker alone, neutral alone, all mixtures) uses the same interval: the pair's
    clips cropped to their common length. A pair with a clip silent in that interval is dropped and counted. Every
    scored clip is peak-normalised; unmixed and masker-alone scores are cached per (clip key, length) in `cache`
    (shareable across classes). The pink arm is mixed PINK_OFFSET_DB below the nominal ratio and uses NOISE_DRAWS
    independent noise realisations per pair, so its pink scores have shape (pairs, NOISE_DRAWS). Pairs without a
    neutral clip get NaN in the neutral arm. Returns (scores, kept_pairs, info); info["unmixed_probs"] holds all four
    class probabilities, aligned with kept_pairs.
    """
    cache = {} if cache is None else cache
    audio = {}

    def clip(key):
        if key not in audio:
            audio[key] = np.asarray(load(key), dtype=np.float64)
        return audio[key]

    def alone(key, length):
        if (key, length) not in cache:
            cache[(key, length)] = np.asarray(score(peak_normalise(clip(key)[:length])), dtype=float)
        return cache[(key, length)]

    rng = np.random.default_rng(seed)
    kept, skipped, worst, offsets, unmixed_probs = [], 0, 0.0, [], []
    rows = {"unmixed": [], "alone_real": [], "alone_pink": [], "alone_neutral_clip": [],
            **{kind: {r: [] for r in ratios} for kind in ("real", "pink", "neutral_clip")}}
    for index, pair in enumerate(pairs):
        if progress and index and index % 25 == 0:
            progress(index, len(pairs))
        t, m = clip(pair.target), clip(pair.masker)
        n = clip(pair.neutral) if pair.neutral else None
        length = min(len(t), len(m), len(n) if n is not None else len(t))
        if min(band_rms(t[:length]), band_rms(m[:length]), band_rms(n[:length]) if n is not None else 1.0) < SILENT_RMS:
            skipped += 1
            continue
        kept.append(pair)
        unmixed_probs.append(alone(pair.target, length))
        rows["unmixed"].append(unmixed_probs[-1][col])
        rows["alone_real"].append(alone(pair.masker, length)[col])
        rows["alone_neutral_clip"].append(alone(pair.neutral, length)[col] if n is not None else np.nan)
        noises = [pink_noise(length, rng) for _ in range(NOISE_DRAWS)]
        rows["alone_pink"].append([float(score(peak_normalise(x))[col]) for x in noises])
        for ratio in ratios:
            for kind, others, offset in (("real", [m[:length]], 0.0), ("pink", noises, PINK_OFFSET_DB),
                                         ("neutral_clip", [n[:length]] if n is not None else None, 0.0)):
                if others is None:
                    rows[kind][ratio].append(np.nan)
                    continue
                values = []
                for other in others:
                    mixed, achieved, fullband = mix_at_ratio(t[:length], other, ratio + offset)
                    worst = max(worst, abs(achieved - (ratio + offset)))
                    offsets.append(fullband - (ratio + offset))
                    values.append(float(score(mixed)[col]))
                rows[kind][ratio].append(values if kind == "pink" else values[0])
    scores = {k: ({r: np.array(v, dtype=float) for r, v in val.items()} if isinstance(val, dict) else np.array(val, dtype=float))
              for k, val in rows.items()}
    if not kept:  # keep the draw axis on the pink arm even with no pairs
        scores["alone_pink"] = scores["alone_pink"].reshape(0, NOISE_DRAWS)
        scores["pink"] = {r: v.reshape(0, NOISE_DRAWS) for r, v in scores["pink"].items()}
    return scores, kept, {
        "pairs_skipped_silent": skipped, "max_band_ratio_error_db": worst,
        "fullband_minus_nominal_db": {"min": float(min(offsets)), "max": float(max(offsets))} if offsets else None,
        "unmixed_probs": np.array(unmixed_probs).reshape(-1, len(unmixed_probs[0]) if unmixed_probs else 4)}


# ---------------------------------------------------------------- driver

def load_labels(data_root):
    instruments = _instruments()
    data = instruments.load_openmic(data_root)
    class_map = json.loads((Path(data_root) / "class-map.json").read_text())
    with np.load(Path(data_root) / "openmic-2018.npz", allow_pickle=False) as npz:
        true = npz["Y_true"][:, [class_map[n] for n in instruments.TARGETS]]
    data["true"] = true
    return data


def all_pairs(data, mode="strict"):
    """Pairs for both classes. No cross-class masker exclusion: scores are cached per clip, so a clip may be a target
    for one class and a masker for the other (each class still excludes its own targets from its masker pool)."""
    return {c: build_pairs(data["keys"], data["true"], data["mask"], data["test_indices"], c, mode=mode) for c in CLASSES}


def _fmt(e):
    return f"{e['value']:+.3f} [{e['ci95'][0]:+.3f},{e['ci95'][1]:+.3f}]"


def format_summary(block):
    lines = [f"pair mode: {block['mode']}" + (f"  ({RELAXED_CAVEAT})" if block["mode"] == "relaxed" else ""), NOTES]
    par = block["parity"]
    lines.append(f"parity: {'evidence present' if par['ok'] else 'UNESTABLISHED: ' + '; '.join(par['reasons'])}"
                 + (f"; GAPS: {'; '.join(par['gaps'])}" if par.get("gaps") else ""))
    for name, res in block["classes"].items():
        g = res["gate0"]
        lines.append(f"\n{name}: n={res['n_pairs']} pairs, {res['distinct_targets']} targets, {res['distinct_maskers']} maskers, "
                     f"{res['distinct_neutrals']} neutrals; threshold {res['threshold']:.2f}; unmixed recall "
                     f"{res['recall']['unmixed']['value']:.3f}")
        avail = block["availability"][name]
        lines.append(f"pairs = reuse x distinct maskers: {res['n_pairs']} pairs from {res['distinct_maskers']} maskers "
                     f"({res['distinct_masker_tracks']} source tracks, max reuse {avail['max_reuse']})"
                     + (f"; UNDER-POWERED vs N={N_PAIRS}" if res["under_powered_vs_n"] else ""))
        lines.append(f"{'dB':>5}" + "".join(f"{k:>16}" for k in ("real", "pink(-12dB)", "neutral_clip")))
        for r in RATIOS:
            lines.append(f"{r:>5}" + "".join(f"{res['recall'][k][r]['value']:>16.3f}" for k in ("real", "pink", "neutral_clip")))
        lines.append("masker-alone rate (distinct clips; target absence not established): " + ", ".join(
            f"{k} {v['value']:.3f} (n={v['n_distinct_clips']})" for k, v in res["masker_alone_rate"].items()))
        for arm in g["arms"].values():
            if not arm.get("available"):
                lines.append(f"  excess vs {arm['control']}: unavailable")
                continue
            loo = arm["leave_one_out"]["masker"]
            lines.append(f"  excess vs {arm['control']} (n={arm['n_pairs']}): {_fmt(arm['excess_drop'])}"
                         + (f"; {arm['flag']} ({arm['saturation']})" if arm["flag"] else "")
                         + (f"; leave-one-masker-out {loo['min']:+.3f}..{loo['max']:+.3f}" if loo.get("n") else ""))
        flips = g.get("target_flips")
        lines.append(f"  Gate 0: {g['verdict']}" + (f" [{g['provisional_reason']}]" if g["provisional"] else "") + f"; {g['conclusion']}"
                     + (f"; target decision flips {flips['flips']}/{flips['n']}" if flips else ""))
        gap = res.get("unmixed_recall_gap")
        if gap:
            lines.append(f"  unmixed recall: stored features {gap['stored_features']:.3f}, re-extracted raw {gap['reextracted_raw']:.3f}, "
                         f"peak-normalised {gap['peak_normalised']:.3f}")
    return "\n".join(lines)


def evaluate_mode(model, data, audio_root, decode, mode, n_boot, parity, fresh_by_key, stored_by_key):
    """Masking curve and Gate 0 for both classes under one pair mode. Fails if selected clips are not extracted."""
    pairs = all_pairs(data, mode)
    missing = sorted(k for c in CLASSES for k in pair_keys(pairs[c][0]) if not audio_path(audio_root, k).is_file())
    if missing:
        raise FileNotFoundError(f"{len(missing)} selected clips are not extracted under {audio_root} (run `extract`): {missing[:3]}...")
    block = {"mode": mode, "parity": parity, "availability": {c: pairs[c][1] for c in CLASSES}, "classes": {},
             "pooling_note": POOLING_NOTE}
    if mode == "relaxed":
        block["caveat"] = RELAXED_CAVEAT
    cache = {}
    for c in CLASSES:
        col = model.classes.index(c)
        thr = float(model.thresholds[col])
        scores, kept, info = collect_scores(pairs[c][0], col, lambda k: decode(audio_path(audio_root, k)),
                                            lambda s: score_samples(model, s), cache=cache,
                                            progress=lambda done, total, c=c: print(f"  {c}: scored {done}/{total} pairs", flush=True))
        info.pop("unmixed_probs")
        targets = sorted({p.target for p in kept})
        flips = None
        if targets and all(k in fresh_by_key for k in targets):
            flips = {"flips": int(sum((stored_by_key[k][col] >= thr) != (fresh_by_key[k][col] >= thr) for k in targets)),
                     "n": len(targets)}
        res = summarise(scores, ids_from_pairs(kept), thr, parity, n_boot, flips)
        res.update(info)
        if targets and all(k in fresh_by_key for k in targets):
            res["unmixed_recall_gap_note"] = ("stored_features and reextracted_raw use the full clip per distinct target; "
                                              "peak_normalised uses each pair's cropped interval")
            res["unmixed_recall_gap"] = {
                "stored_features": float(np.mean([stored_by_key[k][col] >= thr for k in targets])),
                "reextracted_raw": float(np.mean([fresh_by_key[k][col] >= thr for k in targets])),
                "peak_normalised": float(np.mean(scores["unmixed"] >= thr))}
        if mode == "relaxed":
            res["gate0"]["caveat"] = RELAXED_CAVEAT
            fired, names = mean_per_id(scores["alone_real"] >= thr, [p.masker for p in kept])
            res["masker_predicted_target"] = {
                "value": float(np.mean(fired)) if len(fired) else float("nan"), "n_maskers": len(names),
                "note": "model output of the shipped head on OpenMIC maskers, a contamination proxy, not a label"}
        block["classes"][c] = res
    return block


def _json_default(obj):
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"not JSON serialisable: {type(obj).__name__}")


def run(data_root, weights, output_dir, archive_audio_root=None, n_boot=1000, decode=None, pairs_mode="strict",
        model=None, data=None, parity_min_clips=PARITY_MIN_CLIPS):
    """Parity, then one pair mode's curve and Gate 0; replaces that mode's block in masking_curve.json."""
    from ohrin.vggish import decode_mono_16k
    decode = decode or decode_mono_16k
    start = time.perf_counter()
    if model is None:
        from ohrin.instrument_inference import InstrumentModel
        model = InstrumentModel.load(weights_dir=weights)
    data = data if data is not None else load_labels(data_root)
    audio_root = Path(archive_audio_root or Path(data_root) / "audio")
    parity_keys = parity_clip_keys(data, parity_min_clips)
    absent = [k for k in parity_keys if not audio_path(audio_root, k).is_file()]
    if absent:
        raise FileNotFoundError(f"{len(absent)} clips are not extracted under {audio_root} (run `extract`): {absent[:3]}...")
    print(NOTES)
    sub = subset_parity(model, data, data_root, decode)
    sub_summary = None
    block_parity = {}
    if sub:
        rows = parity_rows(*sub, model.thresholds, list(model.classes))
        sub_summary = parity_summary(rows, CLASSES)
        block_parity["audio_subset"] = {"summary": sub_summary, "all_classes_summary": parity_summary(rows), "rows": rows}
        print(format_parity(rows))
    clip_rows, fresh, stored = clip_parity(model, data, parity_keys, lambda k: decode(audio_path(audio_root, k)))
    clip_summary = parity_summary(clip_rows, CLASSES)
    block_parity["clips"] = {"summary": clip_summary, "all_classes_summary": parity_summary(clip_rows),
                             "rows": [r for r in clip_rows if not r["pass"]],
                             "note": "raw decoded clips; summary judges drums and bass, guitar/voice are information only; "
                                     "failing rows only"}
    parity = {**parity_gate(sub_summary, clip_summary, parity_min_clips), **block_parity}
    block = evaluate_mode(model, data, audio_root, decode, pairs_mode, n_boot, parity, fresh, stored)
    block["metadata"] = {
        "timestamp_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "runtime_seconds": time.perf_counter() - start,
        "constants": {"seed": SEED, "n_pairs": N_PAIRS, "max_reuse": MAX_REUSE, "min_neutral_clips": MIN_NEUTRAL_CLIPS,
                      "ratios_db": list(RATIOS), "band_hz": list(BAND_HZ), "peak_dbfs": PEAK_DBFS,
                      "gate_margin": GATE_MARGIN, "gate_baseline_min": GATE_BASELINE_MIN, "saturation": SATURATION,
                      "pink_offset_db": PINK_OFFSET_DB, "noise_draws": NOISE_DRAWS,
                      "parity_tol": PARITY_TOL, "parity_min_clips": parity_min_clips, "bootstrap_resamples": n_boot},
        "ratio_definition": "masker-to-target RMS inside 125-7500 Hz for all signals; full-band offset reported separately",
        "bootstrap": "crossed: target source tracks and masker/neutral clips; percentile 95%",
        "head": "shipped OpenMIC MLP via InstrumentModel.load (outputs/openmic-mlp.pt), stored validation thresholds",
        "thresholds": {c: float(model.thresholds[model.classes.index(c)]) for c in model.classes},
        "notes": NOTES}
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "masking_curve.json"
    doc = {"schema": 3}
    if target.is_file():  # keep only the other mode's block; everything else is rewritten
        try:
            old = json.loads(target.read_text())
            other = [m for m in PAIR_MODES if m != pairs_mode][0]
            if old.get("schema") == 3 and other in old:  # blocks from older rules are dropped, never mixed in
                doc[other] = old[other]
        except ValueError:
            pass
    doc[pairs_mode] = block
    target.write_text(json.dumps(_pooling().to_jsonable(doc), indent=2, allow_nan=False, default=_json_default) + "\n")
    print(format_summary(block))
    return doc


EXIT_ARCHIVE_ABSENT, EXIT_DOWNLOAD_UNFINISHED, EXIT_UNSAFE, EXIT_CORRUPT, EXIT_MISSING_CLIPS = 2, 3, 4, 5, 6


def extract_command(data_root):
    """Extract the clips both pair modes and the parity check need. Returns a process exit code, printing why."""
    archive = Path(data_root) / ARCHIVE
    data = load_labels(data_root)
    wanted = parity_clip_keys(data)
    try:
        result = extract_clips(archive, Path(data_root) / "audio", wanted)
    except FileNotFoundError:
        print(f"Archive not found: {archive}. Download OpenMIC first; nothing was extracted.")
        return EXIT_ARCHIVE_ABSENT
    except DownloadUnfinished as exc:
        print(f"{exc} Nothing was extracted.")
        return EXIT_DOWNLOAD_UNFINISHED
    except UnsafeArchive as exc:
        print(f"Refused: {exc}")
        return EXIT_UNSAFE
    except ArchiveCorrupt as exc:
        print(f"Archive problem: {exc}")
        return EXIT_CORRUPT
    print(json.dumps({"selected": len(wanted), **result}, indent=2))
    if result["missing"]:
        print(f"{len(result['missing'])} requested clips were not found in the archive.")
        return EXIT_MISSING_CLIPS
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("parity", "extract", "run"))
    parser.add_argument("--data-root", type=Path, default=Path("data/openmic-2018"))
    parser.add_argument("--weights", type=Path, default=Path("data/vggish"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/drum-masking"))
    parser.add_argument("--pairs", choices=PAIR_MODES, default="strict", help="run: strict (primary) or relaxed (secondary)")
    parser.add_argument("--bootstraps", type=int, default=1000)
    args = parser.parse_args()
    if args.command == "extract":
        raise SystemExit(extract_command(args.data_root))
    elif args.command == "parity":
        from ohrin.instrument_inference import InstrumentModel
        from ohrin.vggish import decode_mono_16k
        model, data = InstrumentModel.load(weights_dir=args.weights), load_labels(args.data_root)
        sub = subset_parity(model, data, args.data_root, decode_mono_16k)
        if sub is None:
            raise SystemExit("No audio_subset clips found.")
        rows = parity_rows(*sub, model.thresholds, list(model.classes))
        print(format_parity(rows))
        print(json.dumps(parity_summary(rows), indent=2))
    else:
        run(args.data_root, args.weights, args.output_dir, n_boot=args.bootstraps, pairs_mode=args.pairs)
