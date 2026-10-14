import tempfile
import unittest
from pathlib import Path

from ohrin.server import WEB, analyze_upload
from tests.test_analysis import write_tone


class ServerBoundaryTests(unittest.TestCase):
    def test_browser_ui_is_available_to_serve(self):
        page = (WEB / "index.html").read_text()
        self.assertIn("Ohrin", page)
        self.assertIn("Select a passage", page)

    def test_upload_analysis_returns_measured_level_change(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            write_tone(path)
            result = analyze_upload(path.read_bytes(), 0, 4)
        self.assertEqual(result["passage"], {"start_s": 0.0, "end_s": 4.0})
        self.assertGreater(result["windows"][0]["rms_dbfs"] - result["windows"][3]["rms_dbfs"], 15)

    def test_rejects_bad_selection_and_bad_audio(self):
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", -1, 4)
        with self.assertRaises(ValueError):
            analyze_upload(b"bad", 0, 4)


if __name__ == "__main__":
    unittest.main()
