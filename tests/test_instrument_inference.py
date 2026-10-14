import importlib.util
import unittest
from unittest import mock

import numpy as np

import ohrin.instrument_inference as inference
from ohrin.instrument_inference import InstrumentModel

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from torch import nn

    class FakeHead(nn.Module):
        def forward(self, x):  # logit grows with the mean embedding value
            return torch.stack([x.mean(1) * 20 - 5, -x.mean(1) * 20 + 5], dim=1)


class ModelAvailabilityTests(unittest.TestCase):
    def setUp(self):
        inference._cached.clear()

    def tearDown(self):
        inference._cached.clear()

    def test_get_model_reports_missing_files(self):
        with mock.patch.object(InstrumentModel, "load", side_effect=FileNotFoundError("vggish-10086976.pth")):
            model, reason = inference.get_model()
        self.assertIsNone(model)
        self.assertIn("vggish-10086976.pth", reason)

    def test_get_model_reports_missing_torch(self):
        with mock.patch.object(InstrumentModel, "load", side_effect=ImportError("No module named torch")):
            model, reason = inference.get_model()
        self.assertIsNone(model)
        self.assertIn("PyTorch", reason)


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class InstrumentModelTests(unittest.TestCase):
    def setUp(self):
        self.model = InstrumentModel(
            embed=lambda samples: np.full((int(len(samples) // 15360), 128), 200, dtype=np.uint8),
            head=FakeHead(), classes=["guitar", "voice"], thresholds=[0.5, 0.5], name="fake")

    def _predict(self, seconds):
        with mock.patch("ohrin.instrument_inference.decode_mono_16k", return_value=np.zeros(int(seconds * 16000))):
            return self.model.predict("x.wav", 5.0, 5.0 + seconds)

    def test_ten_seconds_is_one_segment_with_thresholded_classes(self):
        out = self._predict(10.0)
        self.assertEqual(len(out["segments"]), 1)
        by_name = {c["name"]: c for c in out["classes"]}
        self.assertTrue(by_name["guitar"]["above_threshold"])
        self.assertFalse(by_name["voice"]["above_threshold"])
        self.assertEqual(out["segments"][0]["start_s"], 5.0)

    def test_twenty_five_seconds_splits_and_drops_nothing_long(self):
        out = self._predict(25.0)
        self.assertEqual(len(out["segments"]), 3)
        self.assertLess(out["segments"][0]["end_s"], out["segments"][1]["end_s"])

    def test_too_short_raises(self):
        with self.assertRaises(ValueError):
            self._predict(0.5)


if __name__ == "__main__":
    unittest.main()
