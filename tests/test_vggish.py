import unittest
from pathlib import Path

import numpy as np
import torch

from ohrin import vggish


class VGGishFrontEndTests(unittest.TestCase):
    def test_mel_matrix_shape_and_support(self):
        m = vggish.mel_matrix()
        self.assertEqual(m.shape, (257, 64))
        self.assertTrue((m >= 0).all())
        self.assertEqual(m[0].sum(), 0)  # DC bin zeroed
        self.assertTrue((m.sum(axis=0) > 0).all())  # every band has support
        hz = np.linspace(0, 8000, 257)
        self.assertEqual(hz[np.flatnonzero(m.sum(axis=1))[0]] >= 100, True)
        self.assertEqual(hz[np.flatnonzero(m.sum(axis=1))[-1]] <= 7600, True)

    def test_ten_seconds_gives_ten_examples(self):
        x = np.random.default_rng(0).normal(size=160000)
        self.assertEqual(vggish.log_mel_examples(x).shape, (10, 96, 64))

    def test_short_clip_gives_no_examples(self):
        self.assertEqual(vggish.log_mel_examples(np.zeros(1000)).shape, (0, 96, 64))

    def test_model_output_shape(self):
        torch.manual_seed(0)
        out = vggish.VGGish().eval()(torch.zeros(2, 1, 96, 64))
        self.assertEqual(tuple(out.shape), (2, 128))

    def test_postprocess_clips_and_quantises(self):
        ex = object.__new__(vggish.Extractor)
        ex.pca_matrix = np.eye(128)
        ex.pca_means = np.zeros((128, 1))
        emb = np.zeros((1, 128)); emb[0, 0] = 10; emb[0, 1] = -10; emb[0, 2] = 0
        q = ex.postprocess(emb)
        self.assertEqual(q.dtype, np.uint8)
        self.assertEqual(list(q[0, :3]), [255, 0, 127])


if __name__ == '__main__':
    unittest.main()
