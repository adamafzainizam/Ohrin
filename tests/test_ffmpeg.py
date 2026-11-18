import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ohrin import analysis, ffmpeg
from tests.test_analysis import write_tone


def fs(*present):
    present = set(present)
    return lambda p: str(p) in present


class DetectionTests(unittest.TestCase):
    def test_detection_order_env_then_path_then_known(self):
        everything = fs("/envdir/ffmpeg", "/usr/bin/ffmpeg")
        which = lambda name: "/on/path/ffmpeg"
        got = ffmpeg.find_tool("ffmpeg", env={"OHRIN_FFMPEG_DIR": "/envdir"}, platform="linux", which=which, is_file=everything)
        self.assertEqual(got, {"path": "/envdir/ffmpeg", "source": "env"})
        got = ffmpeg.find_tool("ffmpeg", env={}, platform="linux", which=which, is_file=everything)
        self.assertEqual(got, {"path": "/on/path/ffmpeg", "source": "path"})
        got = ffmpeg.find_tool("ffmpeg", env={}, platform="linux", which=lambda n: None, is_file=everything)
        self.assertEqual(got, {"path": "/usr/bin/ffmpeg", "source": "known"})
        self.assertIsNone(ffmpeg.find_tool("ffmpeg", env={}, platform="linux", which=lambda n: None, is_file=fs()))

    def test_env_dir_without_the_tool_falls_through(self):
        got = ffmpeg.find_tool("ffprobe", env={"OHRIN_FFMPEG_DIR": "/envdir"}, platform="linux",
                               which=lambda n: "/p/ffprobe", is_file=fs())
        self.assertEqual(got["source"], "path")

    def test_known_dirs_per_platform(self):
        mac = [str(d) for d in ffmpeg.known_dirs("darwin", {})]
        self.assertEqual(mac, ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"])
        lin = [str(d) for d in ffmpeg.known_dirs("linux", {})]
        self.assertEqual(lin, ["/usr/bin", "/usr/local/bin", "/snap/bin"])
        win = [str(d).replace("\\", "/") for d in ffmpeg.known_dirs(
            "win32", {"LOCALAPPDATA": "C:/L", "ProgramFiles": "C:/PF", "USERPROFILE": "C:/U"})]
        self.assertEqual(win, ["C:/L/Microsoft/WinGet/Links", "C:/PF/ffmpeg/bin",
                               "C:/U/scoop/shims", "C:/ProgramData/chocolatey/bin"])

    def test_windows_known_dir_adds_exe(self):
        got = ffmpeg.find_tool("ffmpeg", env={}, platform="win32", which=lambda n: None,
                               is_file=lambda p: str(p).replace("\\", "/") == "C:/ProgramData/chocolatey/bin/ffmpeg.exe")
        self.assertEqual(got["source"], "known")
        self.assertTrue(got["path"].endswith("ffmpeg.exe"))

    def test_missing_tool_status_has_per_os_hint(self):
        for platform, word in (("win32", "winget"), ("darwin", "brew"), ("linux", "apt")):
            status = ffmpeg.status(env={}, platform=platform, which=lambda n: None, is_file=fs())
            self.assertFalse(status["found"])
            self.assertIsNone(status["ffmpeg"])
            self.assertIsNone(status["ffprobe"])
            self.assertIn(word, status["hint"])

    def test_status_found_needs_both(self):
        both = ffmpeg.status(env={}, platform="linux", which=lambda n: f"/p/{n}", is_file=fs())
        self.assertTrue(both["found"])
        one = ffmpeg.status(env={}, platform="linux", which=lambda n: "/p/ffmpeg" if n == "ffmpeg" else None, is_file=fs())
        self.assertFalse(one["found"])
        self.assertEqual(one["ffmpeg"], "/p/ffmpeg")

    def test_windows_lookup_excludes_the_current_directory(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NoDefaultCurrentDirectoryInExePath", None)
            seen = {}
            ffmpeg.find_tool("ffmpeg", env={}, platform="win32",
                             which=lambda n: seen.setdefault("v", os.environ.get("NoDefaultCurrentDirectoryInExePath")),
                             is_file=lambda p: False)
            self.assertEqual(seen["v"], "1")
            self.assertNotIn("C:/ffmpeg/bin", [str(d).replace("\\", "/") for d in ffmpeg.known_dirs("win32", {})])

    def test_tool_raises_ffmpeg_missing_value_error(self):
        with mock.patch.object(ffmpeg, "find_tool", return_value=None):
            with self.assertRaises(ffmpeg.FFmpegMissing) as ctx:
                ffmpeg.tool("ffmpeg")
        self.assertIsInstance(ctx.exception, ValueError)
        self.assertIn("FFmpeg", str(ctx.exception))
        self.assertIn(ffmpeg.install_hint(), str(ctx.exception))
        with mock.patch.object(ffmpeg, "find_tool", return_value={"path": "/x/ffmpeg", "source": "path"}):
            self.assertEqual(ffmpeg.tool("ffmpeg"), "/x/ffmpeg")

    def test_run_kwargs_hide_console_on_windows_only(self):
        self.assertEqual(ffmpeg.run_kwargs("linux"), {})
        self.assertEqual(ffmpeg.run_kwargs("darwin"), {})
        self.assertEqual(ffmpeg.run_kwargs("win32"), {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)})


class AnalysisIntegrationTests(unittest.TestCase):
    def test_analysis_uses_resolved_tool_paths(self):
        seen = []

        def fake_run(args):
            seen.append(list(args))
            return subprocess.CompletedProcess(args, 1, stdout=b"", stderr=b"")

        with mock.patch.object(analysis, "_run", fake_run), \
             mock.patch("ohrin.ffmpeg.tool", side_effect=lambda n: f"/opt/{n}"):
            with self.assertRaises(ValueError):
                analysis.analyze_audio(Path("x.wav"), 0, 4)
        self.assertEqual(seen[0][0], "/opt/ffprobe")

    def test_analyze_upload_without_ffmpeg_is_value_error_with_hint(self):
        from ohrin.server import analyze_upload
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "t.wav"
            write_tone(wav)
            data = wav.read_bytes()
        with mock.patch.object(ffmpeg, "find_tool", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                analyze_upload(data, 0, 4)
        self.assertIn(ffmpeg.install_hint(), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
