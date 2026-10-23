"""Does patch pooling cost drums/bass recall? Compare pooling variants on stored OpenMIC patches.

Spec item 0-pre(ii). Run from project root:
  python3 -m experiments.pooling_test --data-root data/openmic-2018

This is clean, unmasked OpenMIC and OpenMIC clips are one ~10 s block, so it only tests within-block
pooling; it cannot speak to masking. Variants (all use the released 128-64-4 MLP head):
  mean          mean of patches, then head (current; trained by fit_classifier)
  max           elementwise max of patches, then head
  patch_mean    head per patch, mean of probabilities
  patch_max     same trained head, max of probabilities
  attention     softmax-weighted patch average (one class-agnostic linear score), then head
  attention_cls per-class attention: head gives patch logits, Linear(128, 4) scores weight them per class

patch_mean/patch_max train with the clip label on every patch (multiple-instance label noise), so they test
that recipe as well as the pooling. Their early stopping uses patch-level validation loss, the others
clip-level. attention_cls pools logits, not features.

Matched FPR is an oracle test-ROC point, not a deployable operating point: the baseline's FPR is measured
on the test set at its validation threshold, and every variant's threshold (the baseline's too) is then
picked from the test negatives. Bootstrap CIs resample source tracks, average over the 5 seeds and recompute
the baseline FPR and the matched thresholds in each resample; they cover test-track sampling only, not
training-seed or validation-split variation. One convention throughout: predict score >= threshold.
"""

import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch
from torch import nn

from experiments.train_openmic import probabilities as _probabilities
from ohrin.instruments import (
    TARGETS, average_precision, choose_thresholds, fit_classifier,
    grouped_validation_split, load_openmic, make_model, precision_recall_f1,
)

VARIANTS = ("mean", "max", "patch_mean", "patch_max", "attention", "attention_cls")
SEEDS = (42, 43, 44, 45, 46)
FOCUS = ("drums", "bass")
METRICS = ("average_precision", "recall_at_matched_fpr", "achieved_fpr")
NOTES = ("Matched FPR is an oracle test-ROC point (thresholds picked on test negatives, baseline FPR measured on test), "
         "not a deployable operating point. CIs: source-track bootstrap, seed-averaged, baseline FPR and matched "
         "thresholds recomputed per resample; they cover test-track sampling only. Clean unmasked OpenMIC, "
         "within-block pooling only; says nothing about masking. Drums AP is ~0.98, so only bass can move on this data. "
         "CIs average the seeds before resampling (seed variance is not in them): read the per-seed paired deltas beside "
         "them. A bass gain such as patch_max vs mean is a lead only.")


class MaxPoolHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.head = make_model("mlp")

    def forward(self, x):
        return self.head(x.max(dim=1).values)


class AttentionPoolHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.head = make_model("mlp")  # built first so the head matches the other variants for a seed
        self.score = nn.Linear(128, 1)

    def forward(self, x):
        weights = torch.softmax(self.score(x), dim=1)
        return self.head((weights * x).sum(dim=1))


class ClassAttentionHead(nn.Module):
    def __init__(self):
        super().__init__()
        self.head = make_model("mlp")
        self.score = nn.Linear(128, 4)

    def forward(self, x):
        weights = torch.softmax(self.score(x), dim=1)  # (clips, patches, classes)
        return (weights * self.head(x)).sum(dim=1)


def probabilities(model, features):
    model.eval()
    return _probabilities(model, features)


def pool_probabilities(patch_probs, how):
    """Pool per-patch probabilities (clips, patches, classes) to (clips, classes)."""
    return patch_probs.mean(axis=1) if how == "mean" else patch_probs.max(axis=1)


def flatten_patches(patches, y, mask):
    """(clips, P, 128) to (clips * P, 128), repeating each clip's label and mask for its P patches."""
    n = patches.shape[1]
    return patches.reshape(-1, patches.shape[-1]), np.repeat(y, n, axis=0), np.repeat(mask, n, axis=0)


def best_epoch(history):
    """Epoch of the weights fit_classifier restores (same improvement rule)."""
    best, epoch = float("inf"), 0
    for row in history:
        if row["val_loss"] < best - 1e-4:
            best, epoch = row["val_loss"], row["epoch"]
    return epoch


def train_variants(patches, mean_features, y, mask, train, val, test, seed, epochs):
    """Train all variants for one seed; return {variant: (val_probs, test_probs, best_epoch)}."""
    out = {}

    def fit(train_arrays, val_arrays, factory=None):
        model, history = fit_classifier("mlp", *train_arrays, *val_arrays, epochs=epochs, seed=seed, factory=factory)
        return model, best_epoch(history)

    model, best = fit((mean_features[train], y[train], mask[train]), (mean_features[val], y[val], mask[val]))
    out["mean"] = (probabilities(model, mean_features[val]), probabilities(model, mean_features[test]), best)
    for name, factory in (("max", MaxPoolHead), ("attention", AttentionPoolHead), ("attention_cls", ClassAttentionHead)):
        model, best = fit((patches[train], y[train], mask[train]), (patches[val], y[val], mask[val]), factory)
        out[name] = (probabilities(model, patches[val]), probabilities(model, patches[test]), best)
    train_flat = flatten_patches(patches[train], y[train], mask[train])
    val_flat = flatten_patches(patches[val], y[val], mask[val])
    model, history = fit_classifier("mlp", *train_flat, *val_flat, epochs=epochs, seed=seed)
    best = best_epoch(history)
    for how in ("mean", "max"):
        out[f"patch_{how}"] = tuple(pool_probabilities(probabilities(model, patches[i]), how) for i in (val, test)) + (best,)
    return out


def false_positive_rate(labels, scores, threshold):
    """Share of negatives with score >= threshold."""
    negatives = ~np.asarray(labels).astype(bool)  # labels are already binarised at >= 0.5 by load_openmic
    if not negatives.any() or np.isnan(threshold):
        return float("nan")
    return float((np.asarray(scores, dtype=float)[negatives] >= threshold).mean())


def matched_fpr_threshold(labels, scores, target_fpr):
    """Lowest threshold t such that predicting score >= t has false-positive rate <= target_fpr."""
    negatives = np.sort(np.asarray(scores, dtype=float)[~np.asarray(labels).astype(bool)])[::-1]
    if np.isnan(target_fpr):
        return float("nan")
    allowed = int(np.floor(target_fpr * len(negatives) + 1e-9))
    return -np.inf if allowed >= len(negatives) else float(np.nextafter(negatives[allowed], np.inf))


def recall_above(labels, scores, threshold):
    """Recall when predicting score >= threshold (name kept from the strict version it replaced)."""
    positives = np.asarray(scores, dtype=float)[np.asarray(labels).astype(bool)]
    return float((positives >= threshold).mean()) if len(positives) and not np.isnan(threshold) else float("nan")


def track_bootstrap_indices(keys, n_boot, seed=0):
    """Row-position arrays from resampling source tracks (the part of a key before '_') with replacement."""
    # Assumes OpenMIC keys of the form <source track>_<offset>, as grouped_validation_split does.
    groups = np.array([key.split("_")[0] for key in np.asarray(keys).astype(str)])
    _, inverse = np.unique(groups, return_inverse=True)
    members = [np.flatnonzero(inverse == g) for g in range(inverse.max() + 1)]
    rng = np.random.default_rng(seed)
    return [np.concatenate([members[g] for g in rng.integers(len(members), size=len(members))])
            for _ in range(n_boot)]


def percentile_ci(values):
    """95% percentile interval ignoring NaN resamples; [nan, nan] if none are left."""
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    if not len(values):
        return [float("nan"), float("nan")]
    low, high = np.percentile(values, [2.5, 97.5])
    return [float(low), float(high)]


def to_jsonable(value):
    """Recursively turn NaN/inf floats into None so json.dumps(allow_nan=False) cannot fail."""
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_jsonable(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def summarise_class(test_keys, y, mask, col, scores, baseline_thresholds, n_boot):
    """AP, matched-FPR recall and achieved FPR for one class, seed-averaged, with track-bootstrap 95% CIs.

    scores[variant][seed] is (test clips, 4). In every resample (and the full test set) the baseline's
    FPR at its validation threshold baseline_thresholds[seed][col] is re-measured and each variant's
    threshold is re-matched to it. Deltas are paired against "mean" within the same resample.
    """
    valid = np.flatnonzero(mask[:, col])
    labels = y[valid, col]
    subsets = {v: [s[valid, col] for s in scores[v]] for v in scores}
    draws = [np.arange(len(valid))] + track_bootstrap_indices(test_keys[valid], n_boot)

    def metrics(idx):
        lab = labels[idx]
        target = [false_positive_rate(lab, s[idx], baseline_thresholds[i][col]) for i, s in enumerate(subsets["mean"])]
        out = {}
        for v, per_seed in subsets.items():
            thr = [matched_fpr_threshold(lab, s[idx], target[i]) for i, s in enumerate(per_seed)]
            out[v] = {
                "average_precision": np.array([average_precision(lab, s[idx]) for s in per_seed]),
                "recall_at_matched_fpr": np.array([recall_above(lab, s[idx], t) for s, t in zip(per_seed, thr)]),
                "achieved_fpr": np.array([false_positive_rate(lab, s[idx], t) for s, t in zip(per_seed, thr)]),
            }
        return out

    results = [metrics(idx) for idx in draws]
    summary = {}
    for v in subsets:
        row = {}
        for name in METRICS:
            series = np.array([r[v][name].mean() for r in results])
            base = np.array([r["mean"][name].mean() for r in results])
            seed_delta = results[0][v][name] - results[0]["mean"][name]
            row[name] = {"value": float(series[0]), "ci95": percentile_ci(series[1:]),
                         "dropped_nan_resamples": int(np.isnan(series[1:]).sum())}
            row[f"{name}_delta_vs_mean"] = {
                "value": float(series[0] - base[0]), "ci95": percentile_ci(series[1:] - base[1:]),
                "per_seed": [float(d) for d in seed_delta],
                "dropped_nan_resamples": int(np.isnan(series[1:] - base[1:]).sum())}
        summary[v] = row
    return summary


def run(data_root: Path, output_dir: Path, epochs: int = 80, n_boot: int = 1000, seeds=SEEDS):
    torch.set_num_threads(4)
    start = time.perf_counter()
    data = load_openmic(data_root)
    with np.load(Path(data_root) / "openmic-2018.npz", allow_pickle=False) as npz:
        raw = npz["X"]
        if not (raw != 0).any(axis=2).all():
            raise ValueError("Some stored patches are all zero (padding); pooling results would be affected.")
        patches = raw.astype(np.float32) / 255.0
    train_all, test = data["train_indices"], data["test_indices"]
    inner_train, inner_val = grouped_validation_split(data["keys"][train_all], seed=42, fraction=.2)
    train, val = train_all[inner_train], train_all[inner_val]
    x, y, mask, keys = data["features"], data["targets"], data["mask"], data["keys"]

    scores = {v: [] for v in VARIANTS}
    own = {v: [] for v in VARIANTS}
    best_epochs = {v: [] for v in VARIANTS}
    baseline_thresholds = []
    for seed in seeds:
        probs = train_variants(patches, x, y, mask, train, val, test, seed, epochs)
        baseline_thresholds.append(choose_thresholds(y[val], probs["mean"][0], mask[val]))
        for v, (val_p, test_p, best) in probs.items():
            scores[v].append(test_p)
            own[v].append(choose_thresholds(y[val], val_p, mask[val]))
            best_epochs[v].append(best)
        print(f"seed {seed} done ({time.perf_counter() - start:.0f}s)", flush=True)

    classes = {name: summarise_class(keys[test], y[test], mask[test], col, scores, baseline_thresholds, n_boot)
               for col, name in enumerate(TARGETS) if name in FOCUS}
    regression, operating = {}, {}
    for v in VARIANTS:
        regression[v], operating[v] = {}, {}
        for col, name in enumerate(TARGETS):
            ok = mask[test, col]
            per_seed = [average_precision(y[test][ok, col], s[ok, col]) for s in scores[v]]
            regression[v][name] = {"mean": float(np.mean(per_seed)), "per_seed": per_seed}
            prf = [precision_recall_f1(y[test][ok, col], s[ok, col], t[col]) for s, t in zip(scores[v], own[v])]
            fprs = [false_positive_rate(y[test][ok, col], s[ok, col], t[col]) for s, t in zip(scores[v], own[v])]
            operating[v][name] = {"recall": float(np.mean([r[1] for r in prf])), "fpr": float(np.mean(fprs))}
        regression[v]["macro"] = float(np.mean([regression[v][n]["mean"] for n in TARGETS]))
    results = {
        "spec": "0-pre(ii) pooling test", "dataset": "OpenMIC-2018 stored VGGish patches (10 per clip)",
        "notes": NOTES, "seeds": list(seeds),
        "bootstrap": {"resamples": n_boot, "unit": "source track", "ci": "95% percentile",
                      "recomputed_per_resample": "baseline FPR and matched thresholds"},
        "split": "official split01 test (Y_mask observed labels only); thresholds from source-track-grouped 20% validation within train",
        "counts": {"train": len(train), "validation": len(val), "test": len(test)},
        "matching": "predict score >= t; recall at the lowest t whose FPR is <= the mean-pool baseline's FPR at its validation threshold, per seed and class",
        "patch_variants": "one per-patch head trained with the clip label on every patch (patch-level early stopping), pooled two ways at inference",
        "best_epoch": {v: {"per_seed": e, "at_cap": [bool(k >= epochs) for k in e]} for v, e in best_epochs.items()},
        "focus_classes": classes, "all_class_average_precision": regression,
        "validation_threshold_operating_point": operating,
        "runtime_seconds": time.perf_counter() - start,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "pooling_test.json").write_text(json.dumps(to_jsonable(results), indent=2, allow_nan=False) + "\n")
    return results


def format_table(results):
    def ci(e):
        return f"{e['value']:.3f} [{e['ci95'][0]:.3f},{e['ci95'][1]:.3f}]"

    def delta(e):
        seeds = e["per_seed"]
        return f"{e['value']:+.3f} [{e['ci95'][0]:+.3f},{e['ci95'][1]:+.3f}] s{min(seeds):+.3f}..{max(seeds):+.3f}"

    cls = results["focus_classes"]
    lines = [f"{'variant':<14}{'class':<7}{'AP [95% CI]':<22}{'recall@matched FPR [95% CI]':<30}{'FPR':>6}{'macro AP':>10}"]
    for v in VARIANTS:
        for name in FOCUS:
            row = cls[name][v]
            macro = f"{results['all_class_average_precision'][v]['macro']:.3f}" if name == FOCUS[0] else ""
            lines.append(f"{v:<14}{name:<7}{ci(row['average_precision']):<22}{ci(row['recall_at_matched_fpr']):<30}"
                         f"{row['achieved_fpr']['value']:>6.3f}{macro:>10}")
    lines += ["", "Paired delta vs mean (same resamples; s = min..max over seeds)",
              f"{'variant':<14}{'class':<7}{'dAP [95% CI] seeds':<46}{'drecall [95% CI] seeds'}"]
    for v in VARIANTS[1:]:
        for name in FOCUS:
            row = cls[name][v]
            lines.append(f"{v:<14}{name:<7}{delta(row['average_precision_delta_vs_mean']):<46}"
                         f"{delta(row['recall_at_matched_fpr_delta_vs_mean'])}")
    dropped = sum(m["dropped_nan_resamples"] for n in FOCUS for v in VARIANTS for m in cls[n][v].values())
    capped = [v for v, e in results["best_epoch"].items() if any(e["at_cap"])]
    lines += ["", f"Bootstrap resamples dropped as NaN (summed over metrics): {dropped}",
              f"Variants with a best epoch at the epoch cap: {', '.join(capped) or 'none'}",
              "Matched FPR is an oracle test-ROC point, not a deployable operating point. CIs: source-track bootstrap,",
              "seed-averaged, baseline FPR and thresholds recomputed per resample; test-track sampling only.",
              "Seeds are averaged before resampling; per-seed deltas shown beside the CIs. A bass gain (e.g. patch_max) is a lead only.",
              "Drums AP ~0.98 leaves no headroom: only bass can move on this data.",
              "Clean unmasked OpenMIC, within-block pooling only; says nothing about masking."]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/openmic-2018"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs/drum-masking"))
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--bootstraps", type=int, default=1000)
    args = parser.parse_args()
    report = run(args.data_root, args.output_dir, args.epochs, args.bootstraps)
    print(format_table(report))
    print(f"runtime {report['runtime_seconds']:.0f}s")
