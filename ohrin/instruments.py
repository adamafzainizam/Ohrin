"""Masked-label instrument recognition on OpenMIC VGGish embeddings."""

import json
import hashlib
from pathlib import Path

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F


TARGETS = ("guitar", "bass", "drums", "voice")
OFFICIAL_NPZ_SHA256 = "3e5f9252e2f983737d13f87a52b3ee02c8cad357f00531762490b31f18d3e62e"


def load_openmic(root: Path) -> dict:
    """Load the official OpenMIC composite and its artist-disjoint test partition."""
    root = Path(root)
    npz_path = root / "openmic-2018.npz"
    with np.load(npz_path, allow_pickle=False) as data:
        features = data["X"].mean(axis=1).astype(np.float32) / 255.0
        true = data["Y_true"]
        mask = data["Y_mask"]
        try:
            keys = data["sample_key"].astype(str)
        except ValueError as exc:
            if "Object arrays cannot be loaded" not in str(exc):
                raise
            with npz_path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            if digest != OFFICIAL_NPZ_SHA256:
                raise ValueError("Pickled sample keys require the verified official NPZ checksum.") from exc
            with np.load(npz_path, allow_pickle=True) as verified:
                keys = verified["sample_key"].astype(str)
    class_map = json.loads((root / "class-map.json").read_text())
    columns = [class_map[name] for name in TARGETS]
    targets = (true[:, columns] >= 0.5).astype(np.float32)
    observed = mask[:, columns].astype(bool)
    if features.shape != (len(keys), 128) or targets.shape != (len(keys), 4) or observed.shape != targets.shape:
        raise ValueError("Unexpected OpenMIC array shapes.")
    train_keys = set((root / "partitions/split01_train.csv").read_text().splitlines())
    test_keys = set((root / "partitions/split01_test.csv").read_text().splitlines())
    if train_keys & test_keys or set(keys) != train_keys | test_keys:
        raise ValueError("OpenMIC sample keys and published partitions disagree.")
    train_idx = np.flatnonzero(np.isin(keys, list(train_keys)))
    test_idx = np.flatnonzero(np.isin(keys, list(test_keys)))
    return {
        "features": features, "targets": targets, "mask": observed, "keys": keys,
        "train_indices": train_idx, "test_indices": test_idx,
    }


def grouped_validation_split(keys, seed=42, fraction=.2):
    """Return row positions, keeping excerpts of the same source track together."""
    keys = np.asarray(keys).astype(str)
    groups = np.array([key.split("_")[0] for key in keys])
    unique = np.unique(groups)
    if len(unique) < 2:
        raise ValueError("At least two source tracks are needed for validation.")
    shuffled = np.random.default_rng(seed).permutation(unique)
    n_val = max(1, min(len(unique) - 1, round(len(unique) * fraction)))
    is_val = np.isin(groups, shuffled[:n_val])
    return np.flatnonzero(~is_val), np.flatnonzero(is_val)


def masked_bce_loss(logits, targets, mask):
    observed = mask.to(logits.dtype)
    if observed.sum() == 0:
        raise ValueError("A batch needs at least one observed label.")
    raw = F.binary_cross_entropy_with_logits(logits, targets, reduction="none")
    return (raw * observed).sum() / observed.sum()


def average_precision(labels, scores):
    labels = np.asarray(labels).astype(bool)
    scores = np.asarray(scores)
    positives = int(labels.sum())
    if positives == 0:
        return float("nan")
    order = np.argsort(-scores, kind="stable")
    ranked_labels = labels[order]
    ranked_scores = scores[order]
    group_ends = np.r_[np.flatnonzero(np.diff(ranked_scores) != 0), len(labels) - 1]
    true_positives = np.cumsum(ranked_labels)[group_ends]
    new_positives = np.diff(np.r_[0, true_positives])
    precision = true_positives / (group_ends + 1)
    return float(np.sum(precision * new_positives) / positives)


def precision_recall_f1(labels, scores, threshold):
    labels = np.asarray(labels).astype(bool)
    predicted = np.asarray(scores) >= threshold
    tp = int((predicted & labels).sum())
    fp = int((predicted & ~labels).sum())
    fn = int((~predicted & labels).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def choose_thresholds(targets, probabilities, mask):
    thresholds = []
    for col in range(targets.shape[1]):
        valid = mask[:, col]
        if not valid.any():
            thresholds.append(.5)
            continue
        scores = [(precision_recall_f1(targets[valid, col], probabilities[valid, col], t)[2], t)
                  for t in np.linspace(.1, .9, 17)]
        thresholds.append(float(max(scores)[1]))
    return np.array(thresholds, dtype=np.float32)


def evaluate(targets, probabilities, mask, thresholds):
    by_class = {}
    for col, name in enumerate(TARGETS):
        valid = mask[:, col]
        y = targets[valid, col]
        p = probabilities[valid, col]
        if not len(y):
            by_class[name] = {"observed": 0, "error": "no observed test labels"}
            continue
        precision, recall, f1 = precision_recall_f1(y, p, thresholds[col])
        by_class[name] = {
            "observed": int(len(y)), "positive": int(y.sum()),
            "average_precision": average_precision(y, p),
            "precision": precision, "recall": recall, "f1": f1,
            "brier": float(np.mean((p - y) ** 2)),
            "threshold": float(thresholds[col]),
        }
    values = [row["average_precision"] for row in by_class.values() if "average_precision" in row]
    return {"per_class": by_class, "macro_average_precision": float(np.nanmean(values)) if values else None}


def make_model(kind: str):
    if kind == "linear":
        return nn.Linear(128, 4)
    if kind == "mlp":
        return nn.Sequential(nn.Linear(128, 64), nn.ReLU(), nn.Dropout(.15), nn.Linear(64, 4))
    raise ValueError(f"Unknown model type: {kind}")


def fit_classifier(kind, train_x, train_y, train_mask, val_x, val_y, val_mask, *, epochs=80, seed=42):
    """Train a small supervised head on frozen embeddings, selecting by val loss."""
    torch.manual_seed(seed)
    model = make_model(kind)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.008 if kind == "linear" else .003, weight_decay=.01)
    tx = torch.as_tensor(train_x, dtype=torch.float32)
    ty = torch.as_tensor(train_y, dtype=torch.float32)
    tm = torch.as_tensor(train_mask, dtype=torch.float32)
    vx = torch.as_tensor(val_x, dtype=torch.float32)
    vy = torch.as_tensor(val_y, dtype=torch.float32)
    vm = torch.as_tensor(val_mask, dtype=torch.float32)
    history = []
    best = float("inf")
    best_weights = None
    stale = 0
    for epoch in range(epochs):
        model.train()
        optimizer.zero_grad()
        loss = masked_bce_loss(model(tx), ty, tm)
        loss.backward()
        optimizer.step()
        model.eval()
        with torch.no_grad():
            val_loss = float(masked_bce_loss(model(vx), vy, vm))
        history.append({"epoch": epoch + 1, "train_loss": float(loss.detach()), "val_loss": val_loss})
        if val_loss < best - 1e-4:
            best = val_loss
            best_weights = {name: value.detach().clone() for name, value in model.state_dict().items()}
            stale = 0
        else:
            stale += 1
            if stale >= 12:
                break
    model.load_state_dict(best_weights)
    model.eval()
    return model, history
