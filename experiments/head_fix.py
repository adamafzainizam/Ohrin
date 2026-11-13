"""Phase 1-lite: can a head-only change recover drums recall under a louder guitar or bass?

Spec: docs/superpowers/specs/2026-10-16-drum-bass-masking-experiment.md, rev. 3.
Run from the project root, with the Ohrin virtualenv:

  ~/.venvs/ohrin-ml/bin/python -m experiments.head_fix pools
  ~/.venvs/ohrin-ml/bin/python -m experiments.head_fix extract
  ~/.venvs/ohrin-ml/bin/python -m experiments.head_fix run

`pools` only checks the pre-registered mixture pools and the 54/57 pair counts.
`extract` pulls every OpenMIC clip the run needs out of the local archive.
`run` extracts anything still missing, builds one fresh feature path, fits H0/H1/H2
and writes gitignored outputs/drum-masking/head-fix/. It does not replace the shipped head.

Features, for every original and every mixture: decode 16 kHz mono, peak-normalise to
-1 dBFS (mix_at_ratio already does this for mixtures), VGGish, mean of patches / 255.
Stored OpenMIC embeddings are not used. The private reference recording is not used.
"""

import argparse
import datetime
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

TARGETS = ("guitar", "bass", "drums", "voice")
DRUMS = TARGETS.index("drums")
BASS = TARGETS.index("bass")
OTHER = ("guitar", "bass", "voice")
SEED = 42
EXTRA_SEEDS = (43, 44, 45, 46)
RATIOS = (0, 6, 12)
PRIMARY_PAIRS = 54
SECONDARY_PAIRS = 57
BASS_PAIRS = 27
TEST_REUSE = 3
RECALL_GAIN = 0.15
AP_DROP = 0.02
FPR_MAX = 0.05
N_BOOT = 1000
EXPECTED_POOLS = {"train_targets": 683, "train_maskers": 67, "val_targets": 145, "val_maskers": 8}
NOT_FIXABLE = "not fixable by head-level augmentation on OpenMIC"
CONFIG = {
    "seed": SEED, "extra_seeds": list(EXTRA_SEEDS), "ratios_db": list(RATIOS),
    "primary_pairs": PRIMARY_PAIRS, "secondary_pairs": SECONDARY_PAIRS, "bass_pairs": BASS_PAIRS,
    "test_reuse": TEST_REUSE, "recall_gain": RECALL_GAIN, "ap_drop": AP_DROP, "fpr_max": FPR_MAX,
    "bootstrap": "crossed over target source tracks and masker source tracks",
    "feature_path": "decode 16 kHz mono, peak-normalise -1 dBFS, VGGish, mean of patches / 255",
}


def config_hash():
    payload = json.dumps(CONFIG, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def mean_feature(embeddings):
    """One clip vector: mean of the uint8 VGGish patches, divided by 255."""
    embeddings = np.asarray(embeddings)
    if embeddings.ndim != 2 or embeddings.shape[1] != 128 or len(embeddings) == 0:
        raise ValueError("VGGish returned no 128-d patches.")
    return embeddings.astype(np.float64).mean(axis=0) / 255.0


def mixture_class_label(target_observed, target_positive, masker_observed, masker_positive):
    """One class of a mixture. Positive means the binarised OpenMIC label (Y_true >= 0.5).

    Either clip observed positive -> observed, label 1. Both observed and both negative ->
    observed, label 0. Anything else stays unobserved. Absence of an annotation is not a negative.
    """
    if (target_observed and target_positive) or (masker_observed and masker_positive):
        return True, 1.0
    if target_observed and masker_observed:
        return True, 0.0
    return False, 0.0


def mixture_labels(target_observed, target_positive, masker_observed, masker_positive):
    """(observed, labels) shape (4,) for one mixture, in TARGETS order."""
    observed, labels = np.zeros(4, dtype=bool), np.zeros(4, dtype=np.float32)
    for col in range(4):
        observed[col], labels[col] = mixture_class_label(
            bool(target_observed[col]), bool(target_positive[col]),
            bool(masker_observed[col]), bool(masker_positive[col]))
    return observed, labels


def _positive(true, observed, row, name):
    col = TARGETS.index(name)
    return bool(observed[row, col]) and float(true[row, col]) >= 0.5


def _absent(true, observed, row, name):
    col = TARGETS.index(name)
    return bool(observed[row, col]) and float(true[row, col]) <= 0.2


def select_pools(indices, keys, true, observed):
    """Drums-positive targets, and drums-absent guitar/bass maskers, inside one partition.

    Returns (target_keys, masker_keys), each sorted by key. A clip can be in only one of the
    two lists. Source-track exclusion happens later, at assignment.
    """
    keys = np.asarray(keys).astype(str)
    rows = [int(i) for i in np.asarray(indices)]
    rows.sort(key=lambda row: keys[row])
    targets, maskers = [], []
    for row in rows:
        if _positive(true, observed, row, "drums"):
            targets.append(keys[row])
        elif _absent(true, observed, row, "drums") and (
                _positive(true, observed, row, "guitar") or _positive(true, observed, row, "bass")):
            maskers.append(keys[row])
    return targets, maskers


def assign_maskers(target_keys, masker_keys, seed):
    """One masker per target, different source track, least-used first, reuse uncapped.

    Target order is a permutation of the given keys under `seed`, then the masking study's
    _assign breaks remaining ties. The same seed reproduces the same dict.
    """
    from experiments.masking_curve import _assign

    targets = [str(key) for key in target_keys]
    maskers = [str(key) for key in masker_keys]
    if not targets:
        return {}
    rng = np.random.default_rng(seed)
    ordered = [targets[i] for i in rng.permutation(len(targets))]
    chosen = _assign(ordered, maskers, rng, max_reuse=len(targets))
    return {key: chosen[key] for key in targets if key in chosen}


def splits(data):
    """Global row indices for inner train, grouped validation, and the official test split."""
    from ohrin.instruments import grouped_validation_split

    train_all = np.asarray(data["train_indices"])
    inner_train, inner_val = grouped_validation_split(np.asarray(data["keys"])[train_all].astype(str), seed=SEED, fraction=.2)
    return train_all[inner_train], train_all[inner_val], np.asarray(data["test_indices"])


def mixture_plan(data):
    """Assignments for inner-train and validation mixtures. Raises if a target is left without a masker."""
    train, val, _ = splits(data)
    plan = {}
    for name, rows in (("train", train), ("val", val)):
        targets, maskers = select_pools(rows, data["keys"], data["true"], data["mask"])
        assigned = assign_maskers(targets, maskers, SEED)
        missing = [key for key in targets if key not in assigned]
        if missing:
            raise RuntimeError(f"{name} left {len(missing)} drums targets without a masker, for example {missing[:3]}")
        plan[name] = {"targets": targets, "maskers": maskers, "assigned": assigned,
                      "counts": {"targets": len(targets), "maskers": len(maskers), "assigned": len(assigned)}}
    return plan


def check_pools(plan):
    got = {
        "train_targets": plan["train"]["counts"]["targets"], "train_maskers": plan["train"]["counts"]["maskers"],
        "val_targets": plan["val"]["counts"]["targets"], "val_maskers": plan["val"]["counts"]["maskers"],
    }
    if got != EXPECTED_POOLS:
        raise RuntimeError(f"mixture pools {got} differ from the pre-registered {EXPECTED_POOLS}")
    return got


def test_pairs(data):
    """Rev. 2 strict drums pairs (54, other-class targets excluded) and the no-exclusion 57."""
    from experiments.masking_curve import build_pairs, target_candidates

    keys, true, mask, test = data["keys"], data["true"], data["mask"], data["test_indices"]
    bass_targets = target_candidates(keys, true, mask, test, "bass")
    drums_targets = target_candidates(keys, true, mask, test, "drums")
    primary, primary_info = build_pairs(
        keys, true, mask, test, "drums", n_pairs=PRIMARY_PAIRS, seed=SEED, max_reuse=TEST_REUSE,
        mode="strict", exclude=bass_targets)
    secondary, secondary_info = build_pairs(
        keys, true, mask, test, "drums", n_pairs=SECONDARY_PAIRS, seed=SEED, max_reuse=TEST_REUSE, mode="strict")
    bass, bass_info = build_pairs(
        keys, true, mask, test, "bass", n_pairs=BASS_PAIRS, seed=SEED, max_reuse=TEST_REUSE,
        mode="strict", exclude=drums_targets)
    return {"primary": primary, "secondary": secondary, "bass": bass,
            "info": {"primary": primary_info, "secondary": secondary_info, "bass": bass_info}}


def mcnemar_exact(reference, other):
    """Two-sided exact McNemar on paired 0/1 hits. `other` is the new head, `reference` is H0."""
    reference = np.asarray(reference).astype(int)
    other = np.asarray(other).astype(int)
    if reference.shape != other.shape or reference.ndim != 1:
        raise ValueError("McNemar inputs must be a pair of equal 1-d hit arrays.")
    gained = int(np.sum((other == 1) & (reference == 0)))
    lost = int(np.sum((other == 0) & (reference == 1)))
    discordant = gained + lost
    if discordant == 0:
        probability = 1.0
    else:
        tail = sum(math.comb(discordant, i) for i in range(min(gained, lost) + 1))
        probability = min(1.0, 2.0 * tail / (2 ** discordant))
    return {"gained": gained, "lost": lost, "n_discordant": discordant, "p_two_sided": probability}


def stop_checks(recall_gain, ci_low, drums_ap_drop, other_macro_drop, masker_hits, n_maskers):
    """The four pre-registered checks against H0. A pass needs every one of them."""
    if n_maskers <= 0:
        raise ValueError("masker-alone FPR needs at least one masker")
    fpr = masker_hits / n_maskers
    checks = {
        "recall_gain_plus6": {"value": float(recall_gain), "ci_low": float(ci_low),
                              "pass": bool(recall_gain >= RECALL_GAIN and ci_low > 0)},
        "drums_ap_drop": {"value": float(drums_ap_drop), "pass": bool(drums_ap_drop <= AP_DROP)},
        "guitar_bass_voice_macro_drop": {"value": float(other_macro_drop), "pass": bool(other_macro_drop <= AP_DROP)},
        "masker_alone_fpr": {"value": float(fpr), "hits": int(masker_hits), "n": int(n_maskers),
                             "pass": bool(fpr <= FPR_MAX)},
    }
    checks["pass"] = all(checks[name]["pass"] for name in (
        "recall_gain_plus6", "drums_ap_drop", "guitar_bass_voice_macro_drop", "masker_alone_fpr"))
    return checks


def decide(h1_pass, h2_pass, h2_beats_h1):
    """H1 wins ties. H2 is proposed only when it passes and either H1 fails or H2's +6 dB CI clears H1."""
    if h1_pass and not (h2_pass and h2_beats_h1):
        return "ship_drums_threshold"
    if h2_pass:
        return "propose_retrained_head"
    return "not_fixable"


def decision_text(code):
    if code == "ship_drums_threshold":
        return ("H1 passes. Ship the drums threshold change only. Shipping still needs owner approval, "
                "its own change, and the same four checks on raw (not peak-normalised) features.")
    if code == "propose_retrained_head":
        return ("H2 passes and is the head this rule proposes. Shipping still needs owner approval, "
                "its own change, and the same four checks on raw (not peak-normalised) features.")
    return NOT_FIXABLE


def lineage(parent, created):
    return {"sources": ["OpenMIC-2018"], "parent": parent, "created": created,
            "config_hash": config_hash(), "kit_derived": False}


def _hits(probabilities, threshold):
    return (np.asarray(probabilities, dtype=float) >= threshold).astype(int)


def paired_recall(h0_hits, other_hits, target_keys, masker_keys, n_boot=N_BOOT):
    """Mean of (other hit - H0 hit) with a crossed track bootstrap and exact McNemar."""
    from experiments.masking_curve import stat, track_of

    delta = np.asarray(other_hits, dtype=float) - np.asarray(h0_hits, dtype=float)
    factors = ([track_of(key) for key in target_keys], [track_of(key) for key in masker_keys])
    return {"delta": stat(delta, factors, n_boot=n_boot, seed=0), "mcnemar": mcnemar_exact(h0_hits, other_hits),
            "recall": float(np.mean(other_hits)), "h0_recall": float(np.mean(h0_hits))}


def ap_report(labels, probabilities, observed, thresholds):
    """Per-class AP and the guitar/bass/voice macro, on observed labels only."""
    from ohrin.instruments import evaluate

    report = evaluate(labels, probabilities, observed, np.asarray(thresholds, dtype=np.float32))
    per = report["per_class"]
    others = [per[name]["average_precision"] for name in OTHER]
    return {"per_class": {name: per[name]["average_precision"] for name in TARGETS},
            "macro": report["macro_average_precision"],
            "guitar_bass_voice_macro": float(np.mean(others))}


def drops_against(reference, candidate):
    drums = reference["per_class"]["drums"] - candidate["per_class"]["drums"]
    other = reference["guitar_bass_voice_macro"] - candidate["guitar_bass_voice_macro"]
    return float(drums), float(other)


def masker_fpr(probabilities, threshold):
    hits = int(np.sum(_hits(probabilities, threshold)))
    return hits, len(probabilities)


def _load_npz(path):
    if not path.is_file():
        return {}
    with np.load(path, allow_pickle=False) as data:
        keys = data["keys"].astype(str)
        return {key: row for key, row in zip(keys, data["features"])}


def _save_npz(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = np.array(sorted(rows))
    features = np.stack([np.asarray(rows[key], dtype=np.float32) for key in keys])
    temporary = path.with_name(path.stem + ".tmp.npz")
    np.savez_compressed(temporary, keys=keys, features=features)
    temporary.replace(path)


def _embed_clip(samples, embed):
    from experiments.masking_curve import peak_normalise
    return mean_feature(embed(peak_normalise(samples)))


def _original_features(keys, audio_root, embed, cache_path, decode):
    from experiments.masking_curve import audio_path

    rows = _load_npz(cache_path)
    missing = [key for key in keys if key not in rows]
    started = time.perf_counter()
    for done, key in enumerate(missing, start=1):
        rows[key] = _embed_clip(decode(audio_path(audio_root, key)), embed)
        if done % 100 == 0 or done == len(missing):
            _save_npz(cache_path, rows)
            rate = done / max(time.perf_counter() - started, 1e-6)
            print(f"original features {done}/{len(missing)} ({rate:.2f}/s)", flush=True)
    return rows


def _mixture_id(target, masker, ratio):
    return f"{target}|{masker}|{int(ratio)}"


def _mixture_features(assignments, audio_root, embed, cache_path, decode):
    """assignments: iterable of (target, masker, ratio)."""
    from experiments.masking_curve import audio_path, mix_at_ratio

    rows = _load_npz(cache_path)
    pending = [(t, m, r) for t, m, r in assignments if _mixture_id(t, m, r) not in rows]
    audio, errors = {}, []
    started = time.perf_counter()
    for done, (target, masker, ratio) in enumerate(pending, start=1):
        try:
            target_audio = audio.get(target)
            if target_audio is None:
                target_audio = decode(audio_path(audio_root, target))
                audio[target] = target_audio
            masker_audio = audio.get(masker)
            if masker_audio is None:
                masker_audio = decode(audio_path(audio_root, masker))
                audio[masker] = masker_audio
            mixed, achieved, _full = mix_at_ratio(target_audio, masker_audio, ratio)
            rows[_mixture_id(target, masker, ratio)] = mean_feature(embed(mixed))
            errors.append(abs(achieved - ratio))
        except ValueError as exc:
            raise RuntimeError(f"mixture {target} + {masker} at {ratio} dB failed: {exc}") from exc
        if done % 100 == 0 or done == len(pending):
            _save_npz(cache_path, rows)
            rate = done / max(time.perf_counter() - started, 1e-6)
            print(f"mixture features {done}/{len(pending)} ({rate:.2f}/s)", flush=True)
    worst = float(max(errors)) if errors else None
    return rows, worst


def _stack(table, keys):
    missing = [key for key in keys if key not in table]
    if missing:
        raise KeyError(f"{len(missing)} features missing, for example {missing[:3]}")
    return np.stack([np.asarray(table[key], dtype=np.float32) for key in keys])


def _probabilities(model, features):
    import torch
    model.eval()
    with torch.no_grad():
        logits = model(torch.as_tensor(np.asarray(features, dtype=np.float32)))
        return torch.sigmoid(logits).numpy()


def _shipped(checkpoint):
    import torch
    from ohrin.instruments import make_model

    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model = make_model(state["kind"])
    model.load_state_dict(state["state_dict"])
    model.eval()
    thresholds = np.asarray(state["thresholds"], dtype=np.float32)
    return model, thresholds, state


def _save_head(path, kind, state_dict, thresholds, parent, created):
    import torch
    torch.save({"kind": kind, "classes": list(TARGETS), "thresholds": np.asarray(thresholds, dtype=np.float32).tolist(),
                "state_dict": state_dict, "feature_source": CONFIG["feature_path"], **lineage(parent, created)}, path)


def _concat_labels(original_rows, original_labels, original_observed, mixtures):
    """mixtures is a list of (observed, labels) aligned with a feature stack already concatenated by the caller."""
    if not mixtures:
        return original_labels, original_observed
    mix_labels = np.stack([labels for _observed, labels in mixtures])
    mix_observed = np.stack([observed for observed, _labels in mixtures])
    return np.concatenate([original_labels, mix_labels]), np.concatenate([original_observed, mix_observed])


def _fit_h2(train_x, train_y, train_m, val_x, val_y, val_m, seed):
    from ohrin.instruments import choose_thresholds, fit_classifier

    model, history = fit_classifier(
        "mlp", train_x, train_y, train_m, val_x, val_y, val_m, epochs=80, seed=seed)
    thresholds = choose_thresholds(val_y, _probabilities(model, val_x), val_m)
    return model, thresholds, len(history)


def _curve(pairs, feature_table, model, ratio, column=DRUMS):
    keys = [_mixture_id(pair.target, pair.masker, ratio) for pair in pairs]
    return _probabilities(model, _stack(feature_table, keys))[:, column], [pair.target for pair in pairs], [pair.masker for pair in pairs]


def _descriptive(group_pairs, mixtures, model, thresholds, column=DRUMS):
    """Recall per ratio for one target class, scored with that class's column and threshold."""
    out = {}
    for ratio in RATIOS:
        scores, _, _ = _curve(group_pairs, mixtures, model, ratio, column)
        out[str(ratio)] = float(np.mean(_hits(scores, thresholds[column])))
    return out


def _score_head(name, model, thresholds, bundle, h0_hits):
    """Stop-rule numbers for one head on the primary 54 pairs, plus the descriptive curves."""
    plus6, targets, maskers = _curve(bundle["primary"], bundle["mixtures"], model, 6)
    hits = _hits(plus6, thresholds[DRUMS])
    paired = paired_recall(h0_hits, hits, targets, maskers, bundle["n_boot"])
    unmixed = ap_report(bundle["test_y"], _probabilities(model, bundle["test_x"]), bundle["test_m"], thresholds)
    drums_drop, other_drop = drops_against(bundle["h0_ap"], unmixed)
    alone = _probabilities(model, bundle["masker_x"])[:, DRUMS]
    hits_alone, n_alone = masker_fpr(alone, thresholds[DRUMS])
    checks = stop_checks(paired["delta"]["value"], paired["delta"]["ci95"][0], drums_drop, other_drop, hits_alone, n_alone)
    recalls = {}
    for ratio in RATIOS:
        scores, _, _ = _curve(bundle["primary"], bundle["mixtures"], model, ratio)
        recalls[str(ratio)] = float(np.mean(_hits(scores, thresholds[DRUMS])))
    return {"name": name, "thresholds": {label: float(value) for label, value in zip(TARGETS, thresholds)},
            "primary_recall": recalls, "paired_plus6": paired, "unmixed": unmixed,
            "drums_ap_drop": drums_drop, "guitar_bass_voice_macro_drop": other_drop,
            "checks": checks, "pass": checks["pass"]}


def _h0_hits_at_plus6(model, thresholds, bundle):
    scores, _, _ = _curve(bundle["primary"], bundle["mixtures"], model, 6)
    return _hits(scores, thresholds[DRUMS])


def _statement(code, provisional):
    text = decision_text(code)
    if provisional:
        text += " H2 is provisional: a seed from 43-46 would change its pass/fail."
    if code == "not_fixable":
        text += " Ask the owner whether MUSDB18 or Slakh already exist in ~/Downloads, and check their licences before any use."
    return text


def needed_keys(data, plan, pairs):
    keys = {str(key) for key in np.asarray(data["keys"]).astype(str)}
    for block in plan.values():
        keys.update(block["targets"])
        keys.update(block["maskers"])
    for group in pairs.values():
        if group is pairs["info"]:
            continue
        for pair in group:
            keys.add(pair.target)
            keys.add(pair.masker)
    return sorted(keys)


def extract_audio(data_root, keys):
    from experiments.masking_curve import (
        ARCHIVE, ArchiveCorrupt, DownloadUnfinished, UnsafeArchive, extract_clips)

    archive = Path(data_root) / ARCHIVE
    try:
        return extract_clips(archive, Path(data_root) / "audio", keys), 0
    except FileNotFoundError:
        print(f"Archive not found: {archive}")
        return None, 2
    except DownloadUnfinished as exc:
        print(exc)
        return None, 3
    except UnsafeArchive as exc:
        print(f"Refused: {exc}")
        return None, 4
    except ArchiveCorrupt as exc:
        print(f"Archive problem: {exc}")
        return None, 5


def _mixture_records(assigned, labels_of):
    records = []
    for target, masker in assigned.items():
        observed, labels = labels_of(target, masker)
        for ratio in RATIOS:
            records.append((target, masker, ratio, observed, labels))
    return records


def run(data_root, weights, checkpoint, output_dir, n_boot=N_BOOT, decode=None):
    """Full Phase 1-lite. Returns the report dict and writes head_fix.json. Does not ship a head."""
    import os
    import torch
    from ohrin.instruments import TARGETS as live_targets
    from ohrin.vggish import Extractor, decode_mono_16k
    from experiments.masking_curve import load_labels
    from experiments.pooling_test import to_jsonable

    if tuple(live_targets) != TARGETS:
        raise RuntimeError(f"TARGETS drifted: {live_targets}")
    torch.set_num_threads(min(12, os.cpu_count() or 1))
    decode = decode or decode_mono_16k
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    data = load_labels(data_root)
    plan = mixture_plan(data)
    pools = check_pools(plan)
    pairs = test_pairs(data)
    for name, expected in (("primary", PRIMARY_PAIRS), ("secondary", SECONDARY_PAIRS), ("bass", BASS_PAIRS)):
        built = pairs["info"][name]["pairs_built"]
        if built != expected:
            raise RuntimeError(f"{name} pairs built {built}, the pre-registered count is {expected}")
    distinct_maskers = len({pair.masker for pair in pairs["primary"]})
    if distinct_maskers != 18:
        raise RuntimeError(f"primary drums maskers are {distinct_maskers}, the pre-registered set has 18")
    keys = needed_keys(data, plan, pairs)
    print(f"extracting up to {len(keys)} clips from the local archive", flush=True)
    summary, code = extract_audio(data_root, keys)
    if code:
        raise SystemExit(code)
    if summary["missing"]:
        print(f"{len(summary['missing'])} clips are missing from the archive.")
        raise SystemExit(6)
    print(f"audio ready, extracted {summary['extracted']}, already present {summary['already_present']}", flush=True)

    index = {str(key): i for i, key in enumerate(np.asarray(data["keys"]).astype(str))}
    embed = Extractor(weights).embed
    audio_root = Path(data_root) / "audio"
    originals = _original_features(keys, audio_root, embed, output_dir / "original-features.npz", decode)

    def labels_of(target, masker):
        left, right = index[target], index[masker]
        binarised = data["targets"]
        return mixture_labels(data["mask"][left], binarised[left] >= 0.5, data["mask"][right], binarised[right] >= 0.5)

    train_records = _mixture_records(plan["train"]["assigned"], labels_of)
    val_records = _mixture_records(plan["val"]["assigned"], labels_of)
    test_jobs = [(pair.target, pair.masker, ratio) for group in ("primary", "secondary", "bass")
                 for pair in pairs[group] for ratio in RATIOS]
    mixtures, worst_ratio = _mixture_features(
        [(t, m, r) for t, m, r, _o, _l in train_records + val_records] + test_jobs,
        audio_root, embed, output_dir / "mixture-features.npz", decode)

    train_rows, val_rows, test_rows = splits(data)
    def original_matrix(rows):
        return _stack(originals, [str(data["keys"][i]) for i in rows])

    def mixture_matrix(records):
        return _stack(mixtures, [_mixture_id(t, m, r) for t, m, r, _o, _l in records])

    train_x = np.concatenate([original_matrix(train_rows), mixture_matrix(train_records)])
    val_x = np.concatenate([original_matrix(val_rows), mixture_matrix(val_records)])
    train_y, train_m = _concat_labels(train_rows, data["targets"][train_rows], data["mask"][train_rows],
                                     [(o, l) for _t, _m, _r, o, l in train_records])
    val_y, val_m = _concat_labels(val_rows, data["targets"][val_rows], data["mask"][val_rows],
                                  [(o, l) for _t, _m, _r, o, l in val_records])
    created = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    h0_model, h0_thresholds, _state = _shipped(checkpoint)
    h0_ap = ap_report(data["targets"][test_rows], _probabilities(h0_model, original_matrix(test_rows)),
                      data["mask"][test_rows], h0_thresholds)
    primary_maskers = list(dict.fromkeys(pair.masker for pair in pairs["primary"]))
    bundle = {
        "primary": pairs["primary"], "mixtures": mixtures, "n_boot": n_boot,
        "test_y": data["targets"][test_rows], "test_m": data["mask"][test_rows], "test_x": original_matrix(test_rows),
        "masker_x": _stack(originals, primary_maskers), "h0_ap": h0_ap,
    }
    h0_hits = _h0_hits_at_plus6(h0_model, h0_thresholds, bundle)
    h0_report = _score_head("H0", h0_model, h0_thresholds, bundle, h0_hits)

    h1_model, _thresholds, _state = _shipped(checkpoint)
    h1_thresholds = h0_thresholds.copy()
    from ohrin.instruments import choose_thresholds
    tuned = choose_thresholds(val_y, _probabilities(h1_model, val_x), val_m)
    h1_thresholds[DRUMS] = tuned[DRUMS]
    h1_report = _score_head("H1", h1_model, h1_thresholds, bundle, h0_hits)
    _save_head(output_dir / "h1.pt", "mlp", h1_model.state_dict(), h1_thresholds, str(checkpoint), created)

    h2_reports = {}
    h2_model = h2_thresholds = None
    for seed in (SEED, *EXTRA_SEEDS):
        model, thresholds, epochs = _fit_h2(train_x, train_y, train_m, val_x, val_y, val_m, seed)
        report = _score_head(f"H2-seed-{seed}", model, thresholds, bundle, h0_hits)
        report["epochs"] = epochs
        h2_reports[str(seed)] = report
        if seed == SEED:
            h2_model, h2_thresholds = model, thresholds
            _save_head(output_dir / "h2.pt", "mlp", model.state_dict(), thresholds, str(checkpoint), created)
    h2 = h2_reports[str(SEED)]
    provisional = any(h2_reports[str(seed)]["pass"] != h2["pass"] for seed in EXTRA_SEEDS)
    h2_vs_h1 = paired_recall(
        _hits(_curve(pairs["primary"], mixtures, h1_model, 6)[0], h1_thresholds[DRUMS]),
        _hits(_curve(pairs["primary"], mixtures, h2_model, 6)[0], h2_thresholds[DRUMS]),
        [pair.target for pair in pairs["primary"]], [pair.masker for pair in pairs["primary"]], n_boot)
    h2_beats = bool(h2_vs_h1["delta"]["ci95"][0] > 0)
    code_name = decide(h1_report["pass"], h2["pass"], h2_beats)

    report = {
        "phase": "1-lite", "created": created, "config_hash": config_hash(), "config": CONFIG,
        "pools": pools, "audio": summary, "mixture_band_ratio_max_abs_error_db": worst_ratio,
        "pairs": {name: pairs["info"][name] for name in ("primary", "secondary", "bass")},
        "h0": h0_report, "h1": h1_report,
        "h2": {**h2, "seeds": h2_reports, "provisional": provisional, "versus_h1_plus6": h2_vs_h1, "beats_h1": h2_beats},
        "secondary_primary_recall_plus6": {
            "H0": _descriptive(pairs["secondary"], mixtures, h0_model, h0_thresholds)["6"],
            "H1": _descriptive(pairs["secondary"], mixtures, h1_model, h1_thresholds)["6"],
            "H2": _descriptive(pairs["secondary"], mixtures, h2_model, h2_thresholds)["6"],
        },
        "bass_curve_descriptive": {
            "H0": _descriptive(pairs["bass"], mixtures, h0_model, h0_thresholds, BASS),
            "H1": _descriptive(pairs["bass"], mixtures, h1_model, h1_thresholds, BASS),
            "H2": _descriptive(pairs["bass"], mixtures, h2_model, h2_thresholds, BASS),
        },
        "decision": code_name, "statement": _statement(code_name, provisional),
        "runtime_seconds": time.perf_counter() - started,
        "ships_nothing": True,
    }
    destination = output_dir / "head_fix.json"
    destination.write_text(json.dumps(to_jsonable(report), indent=2, allow_nan=False) + "\n")
    print(report["statement"], flush=True)
    print(f"wrote {destination}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Phase 1-lite head fix on OpenMIC. Does not ship a head.")
    parser.add_argument("command", choices=("pools", "extract", "run"))
    parser.add_argument("--data-root", type=Path, default=Path("data/openmic-2018"))
    parser.add_argument("--weights", type=Path, default=Path("data/vggish"))
    parser.add_argument("--checkpoint", type=Path, default=Path("outputs/openmic-mlp.pt"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/drum-masking/head-fix"))
    parser.add_argument("--bootstraps", type=int, default=N_BOOT)
    args = parser.parse_args(argv)
    from experiments.masking_curve import load_labels
    data = load_labels(args.data_root)
    if args.command == "pools":
        plan = mixture_plan(data)
        pools = check_pools(plan)
        pairs = test_pairs(data)
        built = {name: pairs["info"][name]["pairs_built"] for name in ("primary", "secondary", "bass")}
        expected = {"primary": PRIMARY_PAIRS, "secondary": SECONDARY_PAIRS, "bass": BASS_PAIRS}
        if built != expected:
            raise RuntimeError(f"pair counts {built} differ from the pre-registered {expected}")
        distinct_maskers = len({pair.masker for pair in pairs["primary"]})
        if distinct_maskers != 18:
            raise RuntimeError(f"primary drums maskers are {distinct_maskers}, the pre-registered set has 18")
        print(json.dumps({"pools": pools, "pairs": built, "primary_maskers": distinct_maskers}, indent=2))
        return 0
    if args.command == "extract":
        plan = mixture_plan(data)
        check_pools(plan)
        pairs = test_pairs(data)
        summary, code = extract_audio(args.data_root, needed_keys(data, plan, pairs))
        if code:
            return code
        print(json.dumps(summary))
        return 6 if summary["missing"] else 0
    run(args.data_root, args.weights, args.checkpoint, args.output_dir, n_boot=args.bootstraps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
