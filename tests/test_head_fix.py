"""Synthetic checks for Phase 1-lite. No OpenMIC audio and no shipped checkpoint."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

import numpy as np

from experiments.head_fix import (
    BASS, DRUMS, TARGETS, _descriptive, _load_npz, _save_npz, assign_maskers, decide, mean_feature, mcnemar_exact,
    mixture_class_label, mixture_labels, select_pools, stop_checks,
)

HAS_TORCH = importlib.util.find_spec("torch") is not None


class FeatureTests(unittest.TestCase):
    def test_mean_of_patches_is_divided_by_255(self):
        embeddings = np.array([[0, 255] + [0] * 126, [255, 0] + [0] * 126], dtype=np.uint8)
        got = mean_feature(embeddings)
        self.assertAlmostEqual(got[0], 0.5)
        self.assertAlmostEqual(got[1], 0.5)
        self.assertEqual(got.shape, (128,))

    def test_empty_patches_are_rejected(self):
        with self.assertRaises(ValueError):
            mean_feature(np.zeros((0, 128)))

    def test_cache_file_roundtrips(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "original-features.npz"
            _save_npz(path, {"b": np.zeros(128), "a": np.ones(128)})
            got = _load_npz(path)
            self.assertEqual(list(got), ["a", "b"])
            self.assertTrue(np.allclose(got["a"], 1.0))
            self.assertFalse(path.with_name(path.stem + ".tmp.npz").exists())


class LabelTests(unittest.TestCase):
    def test_each_case_in_the_mixture_rule(self):
        self.assertEqual(mixture_class_label(True, True, False, False), (True, 1.0))
        self.assertEqual(mixture_class_label(False, False, True, True), (True, 1.0))
        self.assertEqual(mixture_class_label(True, True, True, True), (True, 1.0))
        self.assertEqual(mixture_class_label(True, False, True, False), (True, 0.0))
        self.assertEqual(mixture_class_label(True, False, False, False), (False, 0.0))
        self.assertEqual(mixture_class_label(False, False, True, False), (False, 0.0))
        self.assertEqual(mixture_class_label(False, False, False, False), (False, 0.0))

    def test_drums_target_is_observed_positive_by_construction(self):
        # guitar, bass, drums, voice. Target is drums-positive and guitar-unobserved.
        # Masker is drums-negative, guitar-positive, bass-unobserved, voice-unobserved.
        observed, labels = mixture_labels(
            [False, False, True, False], [0, 0, 1, 0],
            [True, False, True, False], [1, 0, 0, 0])
        self.assertEqual(list(TARGETS), ["guitar", "bass", "drums", "voice"])
        self.assertTrue(observed[2] and labels[2] == 1.0)
        self.assertTrue(observed[0] and labels[0] == 1.0)
        self.assertFalse(observed[1])
        self.assertFalse(observed[3])

    def test_unannotated_partner_does_not_become_a_negative(self):
        observed, labels = mixture_labels(
            [True, True, True, True], [0, 0, 1, 0],
            [False, False, True, False], [0, 0, 0, 0])
        self.assertFalse(observed[0])
        self.assertFalse(observed[1])
        self.assertEqual(labels[0], 0.0)


class PoolTests(unittest.TestCase):
    def test_pools_stay_inside_the_index_set_and_off_each_other(self):
        keys = np.array(["000001_0", "000002_0", "000003_0", "000009_0"])
        # guitar, bass, drums, voice
        true = np.array([
            [.1, .1, .9, .1],  # drums target
            [.9, .1, .1, .1],  # guitar masker
            [.1, .9, .1, .1],  # bass masker
            [.9, .1, .9, .1],  # drums target outside the partition
        ])
        observed = np.ones_like(true, dtype=bool)
        targets, maskers = select_pools([0, 1, 2], keys, true, observed)
        self.assertEqual(targets, ["000001_0"])
        self.assertEqual(maskers, ["000002_0", "000003_0"])

    def test_soft_drums_score_is_not_an_absent_masker(self):
        keys = np.array(["000004_0"])
        true = np.array([[.9, .1, .3, .1]])  # drums 0.3 is neither >= 0.5 nor <= 0.2
        observed = np.ones((1, 4), dtype=bool)
        self.assertEqual(select_pools([0], keys, true, observed), ([], []))

    def test_assignment_reuses_without_a_cap_and_never_the_same_track(self):
        targets = ["000001_0", "000002_0", "000003_0"]
        chosen = assign_maskers(targets, ["000009_0"], 42)
        self.assertEqual(sorted(chosen), targets)
        self.assertEqual(set(chosen.values()), {"000009_0"})
        blocked = assign_maskers(targets, ["000001_9", "000009_0"], 42)
        self.assertEqual(blocked["000001_0"], "000009_0")
        for target, masker in blocked.items():
            self.assertNotEqual(target.split("_")[0], masker.split("_")[0])

    def test_seed_reproduces_and_changes_the_assignment(self):
        targets = [f"{track:06d}_0" for track in range(10, 20)]
        maskers = [f"{track:06d}_1" for track in range(30, 34)]
        self.assertEqual(assign_maskers(targets, maskers, 42), assign_maskers(targets, maskers, 42))
        self.assertNotEqual(assign_maskers(targets, maskers, 42), assign_maskers(targets, maskers, 43))
        for target, masker in assign_maskers(targets, maskers, 42).items():
            self.assertNotEqual(target[:6], masker[:6])


class StopRuleTests(unittest.TestCase):
    def test_all_four_checks_are_required(self):
        passing = stop_checks(0.15, 0.01, 0.02, 0.02, 0, 18)
        self.assertTrue(passing["pass"])
        self.assertFalse(stop_checks(0.15, 0.0, 0.02, 0.02, 0, 18)["pass"])
        self.assertFalse(stop_checks(0.149, 0.01, 0.02, 0.02, 0, 18)["pass"])
        self.assertFalse(stop_checks(0.15, 0.01, 0.021, 0.02, 0, 18)["pass"])
        self.assertFalse(stop_checks(0.15, 0.01, 0.02, 0.021, 0, 18)["pass"])

    def test_zero_of_eighteen_maskers_passes_and_one_fails(self):
        self.assertTrue(stop_checks(0.2, 0.05, 0.0, 0.0, 0, 18)["masker_alone_fpr"]["pass"])
        failed = stop_checks(0.2, 0.05, 0.0, 0.0, 1, 18)
        self.assertFalse(failed["masker_alone_fpr"]["pass"])
        self.assertGreater(failed["masker_alone_fpr"]["value"], 0.05)
        self.assertFalse(failed["pass"])

    def test_h1_is_kept_unless_h2_passes_and_beats_it(self):
        self.assertEqual(decide(True, True, False), "ship_drums_threshold")
        self.assertEqual(decide(True, False, False), "ship_drums_threshold")
        self.assertEqual(decide(True, True, True), "propose_retrained_head")
        self.assertEqual(decide(False, True, False), "propose_retrained_head")
        self.assertEqual(decide(False, False, False), "not_fixable")

    def test_mcnemar_matches_the_binomial_tail(self):
        quiet = mcnemar_exact([1, 0, 1, 0], [1, 0, 1, 0])
        self.assertEqual(quiet["n_discordant"], 0)
        self.assertEqual(quiet["p_two_sided"], 1.0)
        gained = mcnemar_exact([0] * 8, [1] * 8)
        self.assertEqual(gained["gained"], 8)
        self.assertEqual(gained["lost"], 0)
        self.assertAlmostEqual(gained["p_two_sided"], 2 / 256)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class DescriptiveCurveTests(unittest.TestCase):
    class _Pair:
        def __init__(self, target, masker):
            self.target, self.masker = target, masker

    class _FixedModel:
        """Returns constant logits per class column: drums ~0.88, bass ~0.12."""
        def __init__(self):
            self.logits = [0.0, -2.0, 2.0, 0.0]

        def eval(self):
            return self

        def __call__(self, features):
            import torch
            return torch.tensor(self.logits).repeat(len(features), 1)

    def setUp(self):
        self.pairs = [self._Pair("t1", "m1"), self._Pair("t2", "m2")]
        self.table = {f"{p.target}|{p.masker}|{r}": np.zeros(128) for p in self.pairs for r in (0, 6, 12)}
        self.model = self._FixedModel()

    def test_bass_curve_uses_the_bass_column_and_threshold(self):
        thresholds = [0.5, 0.3, 0.5, 0.5]  # bass prob 0.12 is below 0.3; drums 0.88 is above
        got = _descriptive(self.pairs, self.table, self.model, thresholds, BASS)
        self.assertEqual(got, {"0": 0.0, "6": 0.0, "12": 0.0})
        thresholds[BASS] = 0.1
        got = _descriptive(self.pairs, self.table, self.model, thresholds, BASS)
        self.assertEqual(got, {"0": 1.0, "6": 1.0, "12": 1.0})

    def test_default_column_is_still_drums(self):
        thresholds = [0.5, 0.5, 0.9, 0.5]  # drums prob 0.88 is below 0.9
        self.assertEqual(_descriptive(self.pairs, self.table, self.model, thresholds)["6"], 0.0)
        thresholds[DRUMS] = 0.8
        self.assertEqual(_descriptive(self.pairs, self.table, self.model, thresholds)["6"], 1.0)
