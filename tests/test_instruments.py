import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from ohrin.instruments import (
        TARGETS, average_precision, grouped_validation_split, load_openmic,
        masked_bce_loss, precision_recall_f1, fit_classifier,
    )


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class InstrumentTests(unittest.TestCase):
    def test_masked_loss_ignores_unknown_labels(self):
        logits = torch.tensor([[0.0, 0.0]], requires_grad=True)
        targets = torch.tensor([[1.0, 0.0]])
        mask = torch.tensor([[1.0, 0.0]])
        loss = masked_bce_loss(logits, targets, mask)
        self.assertAlmostEqual(loss.item(), 0.693147, places=5)
        loss.backward()
        self.assertEqual(logits.grad[0, 1].item(), 0.0)

    def test_validation_split_keeps_songs_together(self):
        keys = np.array(['song1_0', 'song1_10', 'song2_0', 'song2_10', 'song3_0', 'song4_0'])
        train, val = grouped_validation_split(keys, seed=7, fraction=.3)
        self.assertFalse(set(keys[train]) & set(keys[val]))
        self.assertFalse({key.split('_')[0] for key in keys[train]} & {key.split('_')[0] for key in keys[val]})

    def test_loader_uses_published_split_and_partial_label_mask(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'partitions').mkdir()
            mapping = {name: i for i, name in enumerate(('guitar', 'bass', 'drums', 'voice'))}
            (root / 'class-map.json').write_text(json.dumps(mapping))
            (root / 'partitions/split01_train.csv').write_text('a_0\nb_0\n')
            (root / 'partitions/split01_test.csv').write_text('c_0\n')
            labels = np.zeros((3, 4), dtype=np.float32)
            labels[0, 0] = .8
            masks = np.zeros((3, 4), dtype=bool)
            masks[0, 0] = True
            np.savez(root / 'openmic-2018.npz', X=np.ones((3, 10, 128), dtype=np.uint8), Y_true=labels, Y_mask=masks, sample_key=np.array(['a_0', 'b_0', 'c_0']))
            dataset = load_openmic(root)
        self.assertEqual(dataset['features'].shape, (3, 128))
        self.assertEqual(dataset['train_indices'].tolist(), [0, 1])
        self.assertEqual(dataset['test_indices'].tolist(), [2])
        self.assertEqual(dataset['targets'][0, TARGETS.index('guitar')], 1)
        self.assertFalse(dataset['mask'][1].any())

    def test_loader_rejects_unverified_pickled_sample_keys(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / 'partitions').mkdir()
            (root / 'class-map.json').write_text(json.dumps({name: i for i, name in enumerate(TARGETS)}))
            (root / 'partitions/split01_train.csv').write_text('a_0\n')
            (root / 'partitions/split01_test.csv').write_text('b_0\n')
            np.savez(root / 'openmic-2018.npz', X=np.ones((2, 10, 128), dtype=np.uint8),
                     Y_true=np.zeros((2, 4)), Y_mask=np.ones((2, 4), dtype=bool),
                     sample_key=np.array(['a_0', 'b_0'], dtype=object))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                load_openmic(root)

    def test_metrics_ignore_unobserved_and_rank_positive_first(self):
        y = np.array([1, 0, 1, 0])
        p = np.array([.9, .2, .8, .99])
        mask = np.array([1, 1, 1, 0], dtype=bool)
        self.assertAlmostEqual(average_precision(y[mask], p[mask]), 1.0)
        precision, recall, f1 = precision_recall_f1(y[mask], p[mask], .5)
        self.assertEqual((precision, recall, f1), (1.0, 1.0, 1.0))

    def test_average_precision_treats_tied_scores_as_one_threshold(self):
        labels = np.array([1, 1, 0, 0])
        scores = np.full(4, .5)
        self.assertAlmostEqual(average_precision(labels, scores), .5)

    def test_linear_training_learns_an_observed_signal(self):
        rng = np.random.default_rng(5)
        features = rng.normal(scale=.05, size=(96, 128)).astype(np.float32)
        features[:, 0] = rng.normal(size=96)
        targets = np.zeros((96, 4), dtype=np.float32)
        targets[:, 0] = (features[:, 0] > 0).astype(np.float32)
        mask = np.zeros_like(targets, dtype=bool)
        mask[:, 0] = True
        model, history = fit_classifier('linear', features[:72], targets[:72], mask[:72],
                                        features[72:], targets[72:], mask[72:], epochs=25, seed=2)
        with torch.no_grad():
            scores = torch.sigmoid(model(torch.from_numpy(features[72:]))).numpy()[:, 0]
        self.assertGreater(average_precision(targets[72:, 0], scores), .7)
        self.assertGreaterEqual(len(history), 1)


if __name__ == '__main__':
    unittest.main()
