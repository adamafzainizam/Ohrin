import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ohrin.server import WEB, analyze_upload
from tests.test_analysis import write_tone


class ServerBoundaryTests(unittest.TestCase):
    def test_browser_ui_is_available_to_serve(self):
        page = (WEB / "index.html").read_text()
        self.assertIn("Ohrin", page)
        self.assertIn("Select a passage", page)

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

    def test_rejects_bad_selection_and_bad_audio(self):
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", -1, 4)
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", 0, 4)


if __name__ == "__main__":
    unittest.main()
