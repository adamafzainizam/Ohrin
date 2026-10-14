"""Train/evaluate Ohrin's bounded instrument task on local OpenMIC files.

Run from project root:
  python3 -m experiments.train_openmic --data-root data/openmic-2018
"""

import argparse
import json
from pathlib import Path
import time

import numpy as np
import torch

from ohrin.instruments import (
    TARGETS, choose_thresholds, evaluate, fit_classifier,
    grouped_validation_split, load_openmic,
)


def probabilities(model, features):
    with torch.no_grad():
        return torch.sigmoid(model(torch.as_tensor(features, dtype=torch.float32))).numpy()


def latency_ms(model, features, repeats=100):
    one = torch.as_tensor(features[:1], dtype=torch.float32)
    with torch.no_grad():
        for _ in range(10):
            model(one)
        start = time.perf_counter()
        for _ in range(repeats):
            model(one)
    return (time.perf_counter() - start) * 1000 / repeats


def error_examples(keys, targets, probabilities_, mask, thresholds):
    examples = {}
    for col, name in enumerate(TARGETS):
        valid = np.flatnonzero(mask[:, col])
        false_pos = valid[(targets[valid, col] == 0) & (probabilities_[valid, col] >= thresholds[col])]
        false_neg = valid[(targets[valid, col] == 1) & (probabilities_[valid, col] < thresholds[col])]
        false_pos = false_pos[np.argsort(-probabilities_[false_pos, col])[:5]]
        false_neg = false_neg[np.argsort(probabilities_[false_neg, col])[:5]]
        examples[name] = {
            "false_positives": [{"key": str(keys[i]), "score": float(probabilities_[i, col])} for i in false_pos],
            "false_negatives": [{"key": str(keys[i]), "score": float(probabilities_[i, col])} for i in false_neg],
        }
    return examples


def run(data_root: Path, output_dir: Path, epochs: int = 80):
    torch.set_num_threads(2)
    data = load_openmic(data_root)
    train_all = data["train_indices"]
    test = data["test_indices"]
    inner_train, inner_val = grouped_validation_split(data["keys"][train_all], seed=42, fraction=.2)
    train, val = train_all[inner_train], train_all[inner_val]
    x, y, mask = data["features"], data["targets"], data["mask"]

    priors = np.array([
        float(y[train, col][mask[train, col]].mean()) for col in range(4)
    ], dtype=np.float32)
    prior_val = np.tile(priors, (len(val), 1))
    prior_thresholds = choose_thresholds(y[val], prior_val, mask[val])
    results = {
        "dataset": "OpenMIC-2018", "feature_source": "provided VGGish embeddings, time-averaged",
        "target_classes": TARGETS, "label_rule": "Y_true >= 0.5 where Y_mask is true",
        "split": "official split01 test; source-track-grouped 20% validation within train",
        "counts": {"train": len(train), "validation": len(val), "test": len(test)},
        "class_prior": evaluate(y[test], np.tile(priors, (len(test), 1)), mask[test], prior_thresholds),
        "models": {},
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for kind in ("linear", "mlp"):
        start = time.perf_counter()
        model, history = fit_classifier(kind, x[train], y[train], mask[train], x[val], y[val], mask[val], epochs=epochs, seed=42)
        val_probs = probabilities(model, x[val])
        thresholds = choose_thresholds(y[val], val_probs, mask[val])
        test_probs = probabilities(model, x[test])
        report = evaluate(y[test], test_probs, mask[test], thresholds)
        report.update({
            "training_seconds": time.perf_counter() - start,
            "epochs": len(history), "cpu_inference_ms_per_10s_embedding": latency_ms(model, x[test]),
            "validation_thresholds": {name: float(thresholds[i]) for i, name in enumerate(TARGETS)},
            "error_examples": error_examples(data["keys"][test], y[test], test_probs, mask[test], thresholds),
        })
        results["models"][kind] = report
        torch.save({"kind": kind, "classes": TARGETS, "thresholds": thresholds.tolist(),
                    "state_dict": model.state_dict(), "feature_source": "OpenMIC VGGish mean / 255"},
                   output_dir / f"openmic-{kind}.pt")
    (output_dir / "openmic-metrics.json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=Path("data/openmic-2018"))
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--epochs", type=int, default=80)
    args = parser.parse_args()
    report = run(args.data_root, args.output_dir, args.epochs)
    print(json.dumps({"counts": report["counts"], "class_prior": report["class_prior"]["macro_average_precision"],
                      "linear": report["models"]["linear"]["macro_average_precision"],
                      "mlp": report["models"]["mlp"]["macro_average_precision"]}, indent=2))
