import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ohrin.server import WEB, FILES, Handler, analyze_upload
from tests.test_analysis import write_tone


class ServerBoundaryTests(unittest.TestCase):
    def test_browser_ui_is_available_to_serve(self):
        page = (WEB / "index.html").read_text()
        self.assertIn("Ohrin", page)
        self.assertIn("Select a passage", page)

    def test_eq_static_routes(self):
        import io
        for path, expected in [("/eq.mjs", 200), ("/eq-ui.js", 200), ("/unknown-eq.js", 404)]:
            handler = object.__new__(Handler)
            handler.path = path
            handler.wfile = io.BytesIO()
            handler.send_response = mock.Mock()
            handler.send_header = mock.Mock()
            handler.end_headers = mock.Mock()
            handler.send_error = mock.Mock()
            handler.do_GET()
            if expected == 200:
                handler.send_response.assert_called_with(200)
                handler.send_header.assert_any_call("Content-Type", "text/javascript; charset=utf-8")
                self.assertTrue(handler.wfile.getvalue())
            else:
                handler.send_error.assert_called()

    def _analyze(self, instruments):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            write_tone(path)
            with mock.patch("ohrin.server.predict_or_unavailable", return_value=instruments):
                return analyze_upload(path.read_bytes(), 0, 4)

    def test_upload_analysis_returns_measured_level_change(self):
        result = self._analyze({"available": False, "reason": "x"})
        self.assertEqual(result["passage"], {"start_s": 0.0, "end_s": 4.0})
        self.assertGreater(result["windows"][0]["rms_dbfs"] - result["windows"][3]["rms_dbfs"], 15)

    def test_unavailable_instruments_keep_identity_limitation(self):
        result = self._analyze({"available": False, "reason": "x"})
        self.assertIs(result["instruments"]["available"], False)
        self.assertTrue(any(t.startswith("Instrument identity") for t in result["limitations"]))

    def test_available_instruments_swap_limitation(self):
        stub = {"available": True, "model": "stub", "classes": [], "segments": [], "caveat": "c"}
        result = self._analyze(stub)
        self.assertFalse(any(t.startswith("Instrument identity") for t in result["limitations"]))
        self.assertTrue(any("uncertain model outputs" in t for t in result["limitations"]))

    def test_prediction_runtime_error_still_yields_analysis_result(self):
        import contextlib, io
        import ohrin.instrument_inference as inference
        model = mock.Mock()
        model.predict.side_effect = RuntimeError("tensor exploded")
        with mock.patch.object(inference, "get_model", return_value=(model, None)), \
                contextlib.redirect_stderr(io.StringIO()):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "tone.wav"
                write_tone(path)
                result = analyze_upload(path.read_bytes(), 0, 4)
        self.assertIs(result["instruments"]["available"], False)
        self.assertIn("windows", result)

    def test_rejects_bad_selection_and_bad_audio(self):
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", -1, 4)
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", 0, 4)


if __name__ == "__main__":
    unittest.main()
