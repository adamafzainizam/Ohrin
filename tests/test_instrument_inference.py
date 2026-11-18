import importlib.util
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

import ohrin.instrument_inference as inference
from ohrin import paths
from ohrin.instrument_inference import InstrumentModel

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from torch import nn

    class FakeHead(nn.Module):
        def forward(self, x):  # logit grows with the mean embedding value
            return torch.stack([x.mean(1) * 20 - 5, -x.mean(1) * 20 + 5], dim=1)


VGGISH_FILES = ("vggish-10086976.pth", "vggish_pca_params.npz")


def touch_vggish(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for name in VGGISH_FILES:
        (directory / name).write_bytes(b"x")


def save_head(path, extra=None):
    """A tiny checkpoint in the experiments/train_openmic.py format."""
    from ohrin.instruments import make_model
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"kind": "mlp", "classes": ("a", "b", "c", "d"), "thresholds": [0.5, 0.4, 0.3, 0.2],
             "state_dict": make_model("mlp").state_dict(), "feature_source": "test"}
    state.update(extra or {})
    torch.save(state, path)


class SearchOrderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.user = self.tmp / "user"
        self.repo = self.tmp / "repo"
        for patcher in (mock.patch.dict("os.environ", {"OHRIN_DATA_DIR": str(self.user)}),
                        mock.patch.object(inference, "ROOT", self.repo),
                        mock.patch.object(paths, "is_frozen", return_value=False)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_search_prefers_user_dir_then_repo(self):
        touch_vggish(self.repo / "data" / "vggish")
        (self.repo / "outputs").mkdir()
        (self.repo / "outputs" / "openmic-mlp.pt").write_bytes(b"x")
        self.assertEqual(inference.resolve_inputs(), (self.repo / "data" / "vggish", self.repo / "outputs" / "openmic-mlp.pt"))
        touch_vggish(self.user / "vggish")
        (self.user / "models").mkdir()
        (self.user / "models" / "openmic-mlp.pt").write_bytes(b"x")
        self.assertEqual(inference.resolve_inputs(), (self.user / "vggish", self.user / "models" / "openmic-mlp.pt"))

    def test_explicit_arguments_win(self):
        touch_vggish(self.user / "vggish")
        touch_vggish(self.tmp / "w")
        explicit = self.tmp / "explicit.pt"
        explicit.write_bytes(b"x")
        weights, ckpt = inference.resolve_inputs(weights_dir=self.tmp / "w", checkpoint=explicit)
        self.assertEqual((weights, ckpt), (self.tmp / "w", explicit))

    def test_frozen_never_uses_repo_paths(self):
        touch_vggish(self.repo / "data" / "vggish")
        (self.repo / "outputs").mkdir()
        (self.repo / "outputs" / "openmic-mlp.pt").write_bytes(b"x")
        with mock.patch.object(paths, "is_frozen", return_value=True):
            with self.assertRaises(FileNotFoundError) as ctx:
                inference.resolve_inputs()
        self.assertIn("vggish", str(ctx.exception))

    def test_vggish_dir_needs_both_files(self):
        (self.user / "vggish").mkdir(parents=True)
        (self.user / "vggish" / VGGISH_FILES[0]).write_bytes(b"x")
        touch_vggish(self.repo / "data" / "vggish")
        (self.repo / "outputs").mkdir()
        (self.repo / "outputs" / "openmic-mlp.pt").write_bytes(b"x")
        weights, _ = inference.resolve_inputs()
        self.assertEqual(weights, self.repo / "data" / "vggish")

    def test_missing_checkpoint_raises_its_name(self):
        touch_vggish(self.user / "vggish")
        with self.assertRaises(FileNotFoundError) as ctx:
            inference.resolve_inputs()
        self.assertEqual(str(ctx.exception), "openmic-mlp.pt")

    def test_missing_reason_points_at_setup_panel(self):
        inference.reset_model_cache()
        self.addCleanup(inference.reset_model_cache)
        _, reason = inference.get_model()
        self.assertIn("Missing local file:", reason)
        self.assertIn("setup panel", reason)
        self.assertIn("docs/model-card.md", reason)

    @unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
    def test_head_checkpoint_loads_weights_only(self):
        touch_vggish(self.user / "vggish")
        save_head(self.user / "models" / "openmic-mlp.pt")
        fake = mock.Mock()
        with mock.patch("ohrin.vggish.Extractor", return_value=fake), \
                mock.patch("torch.load", wraps=torch.load) as spy:
            model = InstrumentModel.load()
        self.assertIs(spy.call_args.kwargs["weights_only"], True)
        self.assertEqual(model.classes, ["a", "b", "c", "d"])
        self.assertIs(model.embed, fake.embed)

    @unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
    def test_pickled_object_checkpoint_is_rejected_and_reported(self):
        touch_vggish(self.user / "vggish")
        save_head(self.user / "models" / "openmic-mlp.pt", {"feature_source": PickledThing()})
        inference.reset_model_cache()
        self.addCleanup(inference.reset_model_cache)
        with mock.patch("ohrin.vggish.Extractor", return_value=mock.Mock()), \
                mock.patch("sys.stderr", new=__import__("io").StringIO()):
            model, reason = inference.get_model()
        self.assertIsNone(model)
        self.assertIn("could not be loaded", reason)


class PickledThing:
    pass


class CacheTests(unittest.TestCase):
    def tearDown(self):
        inference.reset_model_cache()

    def test_reset_model_cache_forces_reload(self):
        inference.reset_model_cache()
        with mock.patch.object(InstrumentModel, "load", side_effect=[FileNotFoundError("a"), "model"]) as load:
            first = inference.get_model()
            self.assertEqual(inference.get_model(), first)
            self.assertEqual(load.call_count, 1)
            inference.reset_model_cache()
            self.assertEqual(inference.get_model(), ("model", None))
            self.assertEqual(load.call_count, 2)

    def test_concurrent_get_model_loads_once(self):
        inference.reset_model_cache()
        count = []

        def slow_load():
            count.append(1)
            time.sleep(0.2)
            return "model"

        results = []
        with mock.patch.object(InstrumentModel, "load", side_effect=slow_load):
            threads = [threading.Thread(target=lambda: results.append(inference.get_model())) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(len(count), 1)
        self.assertEqual(results, [("model", None)] * 2)


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

    def test_other_import_errors_are_not_reported_as_missing_torch(self):
        err = ImportError("No module named 'scipy'", name="scipy")
        with mock.patch.object(InstrumentModel, "load", side_effect=err):
            model, reason = inference.get_model()
        self.assertIsNone(model)
        self.assertNotIn("PyTorch is not installed", reason)
        self.assertIn("scipy", reason)

    def test_broken_torch_install_is_not_reported_as_missing(self):
        err = ImportError("libtorch_cpu.so: cannot open shared object file", name="libtorch_cpu.so")
        with mock.patch.object(InstrumentModel, "load", side_effect=err):
            _, reason = inference.get_model()
        self.assertNotIn("not installed", reason)
        self.assertIn("PyTorch failed to import", reason)

    def test_torch_submodule_import_error_is_reported_as_missing_torch(self):
        err = ImportError("No module named 'torch.nn'", name="torch.nn")
        with mock.patch.object(InstrumentModel, "load", side_effect=err):
            _, reason = inference.get_model()
        self.assertIn("PyTorch is not installed", reason)

    def test_load_failure_is_logged_to_stderr(self):
        import contextlib, io
        buf = io.StringIO()
        with mock.patch.object(InstrumentModel, "load", side_effect=RuntimeError("boom")), \
                contextlib.redirect_stderr(buf):
            inference.get_model()
        self.assertIn("RuntimeError", buf.getvalue())
        self.assertIn("Traceback", buf.getvalue())

    def test_unexpected_prediction_error_becomes_unavailable_and_is_logged(self):
        import contextlib, io
        model = mock.Mock()
        model.predict.side_effect = RuntimeError("CUDA out of memory secret-path")
        buf = io.StringIO()
        inference._cached["result"] = (model, None)
        with contextlib.redirect_stderr(buf):
            out = inference.predict_or_unavailable("x.wav", 0.0, 10.0)
        self.assertFalse(out["available"])
        self.assertNotIn("secret-path", out["reason"])
        self.assertIn("Traceback", buf.getvalue())
        self.assertIn("secret-path", buf.getvalue())

    def test_torch_import_error_by_name_is_reported_as_missing_torch(self):
        err = ImportError("No module named 'torch'", name="torch")
        with mock.patch.object(InstrumentModel, "load", side_effect=err):
            _, reason = inference.get_model()
        self.assertIn("PyTorch is not installed", reason)

    def test_corrupt_checkpoint_becomes_unavailable_with_reason(self):
        with mock.patch.object(InstrumentModel, "load", side_effect=RuntimeError("PytorchStreamReader failed")):
            out = inference.predict_or_unavailable("x.wav", 0.0, 10.0)
        self.assertFalse(out["available"])
        self.assertIn("could not be loaded", out["reason"])
        self.assertIn("restart", out["reason"])

    @unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
    def test_unusable_weights_and_checkpoint_files_are_reported_not_raised(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for name in ("vggish-10086976.pth", "vggish_pca_params.npz"):
                (d / name).write_bytes(b"x")
            ckpt = d / "bad.pt"
            ckpt.write_bytes(b"not a checkpoint")
            real_load = InstrumentModel.load.__func__
            with mock.patch.object(InstrumentModel, "load",
                                   classmethod(lambda cls: real_load(cls, weights_dir=d, checkpoint=ckpt))):
                model, reason = inference.get_model()
        self.assertIsNone(model)
        self.assertTrue(reason)
        self.assertNotIn("PyTorch is not installed", reason)


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
