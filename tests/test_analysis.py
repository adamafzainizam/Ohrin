import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from ohrin.analysis import analyze_audio


def write_tone(path: Path, seconds=4.0):
    sr = 22050
    t = np.arange(int(seconds * sr)) / sr
    signal = np.sin(2 * np.pi * 220 * t)
    signal[t >= seconds / 2] *= 0.1
    pcm = (signal * 12000).astype("<i2")
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sr)
        f.writeframes(pcm.tobytes())


class AnalysisTests(unittest.TestCase):
    def test_detects_level_drop_in_selected_passage(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "with spaces.wav"
            write_tone(path)
            result = analyze_audio(path, 0, 4)
        self.assertAlmostEqual(result["duration_s"], 4, places=1)
        self.assertEqual(result["passage"], {"start_s": 0.0, "end_s": 4.0})
        self.assertGreater(result["windows"][0]["rms_dbfs"] - result["windows"][3]["rms_dbfs"], 15)
        self.assertEqual(result["candidate_chords"][0]["status"], "uncertain")

    def test_clamps_end_to_decoded_duration(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            write_tone(path)
            result = analyze_audio(path, 1, 100)
        self.assertLessEqual(result["passage"]["end_s"], result["duration_s"] + 0.01)
        self.assertTrue(all(w["end_s"] <= result["passage"]["end_s"] + 0.01 for w in result["windows"]))

    def test_rejects_invalid_or_corrupt_audio(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "broken.mp3"
            path.write_bytes(b"not audio")
            with self.assertRaises(ValueError):
                analyze_audio(path, 0, 4)
            path.write_bytes(b"")
            with self.assertRaises(ValueError):
                analyze_audio(path, -1, 4)

    def test_rejects_passage_longer_than_three_minutes(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "tone.wav"
            write_tone(path, seconds=181)
            with self.assertRaisesRegex(ValueError, "three minutes"):
                analyze_audio(path, 0, 181)


if __name__ == "__main__":
    unittest.main()
