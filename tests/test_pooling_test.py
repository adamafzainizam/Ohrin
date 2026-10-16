import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from experiments.pooling_test import (
        AttentionPoolHead, ClassAttentionHead, MaxPoolHead, false_positive_rate, flatten_patches,
        format_table, matched_fpr_threshold, percentile_ci, pool_probabilities, probabilities,
        recall_above, run, summarise_class, to_jsonable, track_bootstrap_indices,
    )
    from ohrin.instruments import fit_classifier, make_model


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class PoolingTests(unittest.TestCase):
    def test_probability_pooling_mean_and_max(self):
        patches = np.array([[[.2, .0], [.6, 1.0]]])
        np.testing.assert_allclose(pool_probabilities(patches, "mean"), [[.4, .5]])
        np.testing.assert_allclose(pool_probabilities(patches, "max"), [[.6, 1.0]])

    def test_max_pool_head_sees_a_single_loud_patch(self):
        head = MaxPoolHead().eval()
        quiet = torch.zeros(1, 3, 128)
        spiky = quiet.clone()
        spiky[0, 1] = 1.0
        self.assertTrue(torch.equal(head(spiky), head.head(torch.ones(1, 128))))
        self.assertEqual(head(quiet).shape, (1, 4))

    def test_attention_weights_sum_to_one_and_reduce_to_mean_when_scores_are_equal(self):
        head = AttentionPoolHead().eval()
        with torch.no_grad():
            head.score.weight.zero_()
            head.score.bias.zero_()
        x = torch.rand(2, 10, 128)
        self.assertTrue(torch.allclose(head(x), head.head(x.mean(dim=1)), atol=1e-6))

    def test_matched_fpr_threshold_and_recall(self):
        labels = np.array([0, 0, 0, 0, 1, 1, 1, 1])
        scores = np.array([.1, .2, .3, .6, .5, .7, .8, .9])
        threshold = matched_fpr_threshold(labels, scores, .25)
        self.assertGreater(threshold, .3)
        self.assertLessEqual(threshold, .5)
        self.assertAlmostEqual(false_positive_rate(labels, scores, threshold), .25)
        self.assertAlmostEqual(recall_above(labels, scores, threshold), 1.0)
        self.assertEqual(matched_fpr_threshold(labels, scores, 1.0), -np.inf)
        self.assertAlmostEqual(recall_above(labels, scores, matched_fpr_threshold(labels, scores, 0.0)), .75)

    def test_threshold_convention_is_greater_or_equal(self):
        labels = np.array([0, 1])
        scores = np.array([.4, .5])
        self.assertEqual(recall_above(labels, scores, .5), 1.0)
        self.assertEqual(false_positive_rate(labels, scores, .4), 1.0)

    def test_baseline_matched_recall_equals_recall_at_its_own_threshold(self):
        labels = np.array([0, 0, 0, 0, 1, 1, 1, 1])
        scores = np.array([.1, .2, .3, .6, .7, .8, .9, .95])
        own = .65
        target = false_positive_rate(labels, scores, own)
        self.assertEqual(target, 0.0)
        matched = matched_fpr_threshold(labels, scores, target)
        self.assertEqual(recall_above(labels, scores, matched), recall_above(labels, scores, own))

    def test_nan_target_fpr_gives_nan_not_a_crash(self):
        labels = np.array([1, 1])
        scores = np.array([.3, .4])
        target = false_positive_rate(labels, scores, .5)
        self.assertTrue(np.isnan(target))
        self.assertTrue(np.isnan(matched_fpr_threshold(labels, scores, target)))

    def test_matched_fpr_respects_ties(self):
        labels = np.array([0, 0, 0, 0, 1, 1])
        scores = np.array([.5, .5, .5, .5, .5, .9])
        threshold = matched_fpr_threshold(labels, scores, .25)
        predicted = scores >= threshold
        self.assertTrue(predicted[5] and not predicted[4])
        self.assertLessEqual(predicted[labels == 0].mean(), .25)

    def test_bootstrap_resamples_whole_source_tracks_and_is_reproducible(self):
        keys = np.array(["a_0", "a_10", "b_0", "c_0", "c_10", "c_20"])
        draws = track_bootstrap_indices(keys, 20, seed=3)
        again = track_bootstrap_indices(keys, 20, seed=3)
        for draw, repeat in zip(draws, again):
            np.testing.assert_array_equal(draw, repeat)
            counts = np.bincount(draw, minlength=6)
            # every track appears as a whole: all its rows share one count
            self.assertEqual(len(set(counts[:2])), 1)
            self.assertEqual(len(set(counts[3:])), 1)

    def test_percentile_ci_orders_bounds(self):
        low, high = percentile_ci(np.arange(101.0))
        self.assertAlmostEqual(low, 2.5)
        self.assertAlmostEqual(high, 97.5)

    def test_summary_reports_gap_between_a_better_and_a_baseline_variant(self):
        rng = np.random.default_rng(0)
        n = 120
        keys = np.array([f"t{i // 2}_{i % 2}" for i in range(n)])
        y = np.zeros((n, 4), dtype=np.float32)
        y[:, 1] = rng.integers(0, 2, n)
        mask = np.ones((n, 4), dtype=bool)
        good = np.tile(y[:, [1]] * .6 + .2, (1, 4)).astype(np.float32)
        noise = rng.random((n, 4)).astype(np.float32)
        scores = {"mean": [noise], "max": [good]}
        out = summarise_class(keys, y, mask, 1, scores, [[.5] * 4], 50)
        self.assertAlmostEqual(out["max"]["average_precision"]["value"], 1.0)
        self.assertGreater(out["max"]["average_precision_delta_vs_mean"]["ci95"][0], 0)
        self.assertEqual(out["mean"]["average_precision_delta_vs_mean"]["value"], 0)
        self.assertAlmostEqual(out["max"]["recall_at_matched_fpr"]["value"], 1.0)
        self.assertEqual(len(out["max"]["average_precision_delta_vs_mean"]["per_seed"]), 1)
        self.assertEqual(out["max"]["achieved_fpr"]["dropped_nan_resamples"], 0)

    def test_json_is_nan_safe(self):
        text = json.dumps(to_jsonable({"a": float("nan"), "b": [np.float32("inf"), np.float64(.5)], "c": np.int64(2)}),
                          allow_nan=False)
        self.assertEqual(json.loads(text), {"a": None, "b": [None, .5], "c": 2})

    def test_percentile_ci_skips_nan_and_handles_all_nan(self):
        self.assertEqual(percentile_ci([1.0, float("nan"), 1.0]), [1.0, 1.0])
        self.assertTrue(np.isnan(percentile_ci([float("nan")])[0]))

    def test_flatten_patches_keeps_labels_aligned(self):
        patches = np.arange(2 * 3 * 128, dtype=np.float32).reshape(2, 3, 128)
        y = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=np.float32)
        mask = np.array([[1, 0, 1, 1], [0, 1, 1, 0]], dtype=bool)
        flat, fy, fm = flatten_patches(patches, y, mask)
        self.assertEqual(flat.shape, (6, 128))
        np.testing.assert_array_equal(flat[4], patches[1, 1])
        np.testing.assert_array_equal(fy[:3], np.tile(y[0], (3, 1)))
        np.testing.assert_array_equal(fm[3:], np.tile(mask[1], (3, 1)))

    def test_factory_builds_model_after_seeding_and_heads_share_init(self):
        rng = np.random.default_rng(0)
        x = rng.random((16, 3, 128)).astype(np.float32)
        y = np.zeros((16, 4), dtype=np.float32)
        y[::2] = 1
        m = np.ones((16, 4), dtype=bool)
        a, _ = fit_classifier("mlp", x, y, m, x, y, m, epochs=1, seed=5, factory=AttentionPoolHead)
        b, _ = fit_classifier("mlp", x, y, m, x, y, m, epochs=1, seed=5, factory=MaxPoolHead)
        self.assertEqual(a.head[0].weight.shape, b.head[0].weight.shape)
        torch.manual_seed(5)
        fresh = AttentionPoolHead()
        torch.manual_seed(5)
        other = MaxPoolHead()
        self.assertTrue(torch.equal(fresh.head[0].weight, other.head[0].weight))
        self.assertEqual(ClassAttentionHead().eval()(torch.rand(2, 3, 128)).shape, (2, 4))

    def test_probabilities_puts_model_in_eval_mode(self):
        model = make_model("mlp").train()
        probabilities(model, np.zeros((1, 128), dtype=np.float32))
        self.assertFalse(model.training)

    def test_fit_classifier_reports_a_never_finite_validation_loss(self):
        x = np.full((4, 128), np.nan, dtype=np.float32)
        y = np.zeros((4, 4), dtype=np.float32)
        m = np.ones((4, 4), dtype=bool)
        with self.assertRaisesRegex(ValueError, "never became finite"):
            fit_classifier("mlp", x, y, m, x, y, m, epochs=2)

    def test_end_to_end_on_a_synthetic_npz(self):
        rng = np.random.default_rng(1)
        keys = np.array([f"t{i // 2}_{i % 2}" for i in range(60)])
        labels = rng.integers(0, 2, (60, 4)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "partitions").mkdir()
            (root / "class-map.json").write_text(json.dumps({n: i for i, n in enumerate(("guitar", "bass", "drums", "voice"))}))
            (root / "partitions/split01_train.csv").write_text("\n".join(keys[:40]) + "\n")
            (root / "partitions/split01_test.csv").write_text("\n".join(keys[40:]) + "\n")
            np.savez(root / "openmic-2018.npz", X=rng.integers(1, 255, (60, 10, 128)).astype(np.uint8),
                     Y_true=labels, Y_mask=np.ones((60, 4), dtype=bool), sample_key=keys)
            out = root / "out"
            report = run(root, out, epochs=3, n_boot=5, seeds=(1, 2))
            saved = json.loads((out / "pooling_test.json").read_text())
            self.assertIn("oracle", saved["notes"])
            self.assertIn("drums", saved["focus_classes"])
            self.assertIn("Paired delta", format_table(report))
            np.savez(root / "openmic-2018.npz", X=np.zeros((60, 10, 128), dtype=np.uint8),
                     Y_true=labels, Y_mask=np.ones((60, 4), dtype=bool), sample_key=keys)
            with self.assertRaisesRegex(ValueError, "all zero"):
                run(root, out, epochs=1, n_boot=2, seeds=(1,))


if __name__ == "__main__":
    unittest.main()
