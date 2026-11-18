import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ohrin import selftest, server

HAS_TORCH = importlib.util.find_spec("torch") is not None


def run_capturing(case, **kw):
    out = mock.patch("sys.stdout", new_callable=__import__("io").StringIO)
    stream = out.start()
    case.addCleanup(out.stop)
    code = selftest.run(**kw)
    return code, stream.getvalue()


class SelfTestTests(unittest.TestCase):
    def test_selftest_passes_from_source(self):
        code, text = run_capturing(self)
        report = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(code, 0, report)
        self.assertTrue(report["ok"])
        for name in ("server", "files", "setup", "analysis", "torch", "webview"):
            self.assertIn(name, report["checks"])
        self.assertEqual(report["checks"]["files"]["status"], "ok")
        self.assertEqual(report["checks"]["setup"]["status"], "ok")
        if HAS_TORCH:
            self.assertEqual(report["checks"]["torch"]["status"], "ok")
        else:
            self.assertEqual(report["checks"]["torch"]["status"], "skipped")

    def test_selftest_fails_when_a_web_file_is_missing(self):
        with tempfile.TemporaryDirectory() as d:
            copy = Path(d) / "web"
            shutil.copytree(server.WEB, copy)
            (copy / "app.js").unlink()
            with mock.patch.object(server, "WEB", copy):
                code, text = run_capturing(self)
        report = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertFalse(report["ok"])
        self.assertEqual(report["checks"]["files"]["status"], "fail")
        self.assertIn("/app.js", report["checks"]["files"]["detail"])

    def test_selftest_requires_torch_when_frozen(self):
        real = importlib.util.find_spec

        def fake(name, *a, **k):
            return None if name == "torch" else real(name, *a, **k)

        with mock.patch.object(selftest.paths, "is_frozen", return_value=True), \
                mock.patch.object(selftest.importlib.util, "find_spec", fake):
            code, text = run_capturing(self)
        report = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(code, 1)
        self.assertEqual(report["checks"]["torch"]["status"], "fail")

    def test_selftest_skips_analysis_without_ffmpeg(self):
        missing = {"found": False, "ffmpeg": None, "ffprobe": None, "hint": "x"}
        with mock.patch.object(selftest.ffmpeg, "status", return_value=missing), \
                mock.patch.object(server.ffmpeg, "status", return_value=missing):
            code, text = run_capturing(self)
        report = json.loads(text.strip().splitlines()[-1])
        self.assertEqual(report["checks"]["analysis"]["status"], "skipped")
        self.assertEqual(code, 0, report)

    def test_selftest_writes_report_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "st.json"
            code, text = run_capturing(self, report_path=str(path))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             json.loads(text.strip().splitlines()[-1]))
        self.assertEqual(code, 0)

    def test_selftest_works_without_stdout(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "st.json"
            with mock.patch("sys.stdout", None):
                code = selftest.run(str(path))
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(path.read_text(encoding="utf-8"))["ok"])


class AppWiringTests(unittest.TestCase):
    def test_app_self_test_flag_runs_selftest(self):
        from ohrin import app
        with mock.patch.object(selftest, "run", return_value=7) as run:
            self.assertEqual(app.main(["--self-test", "--self-test-report", "x.json"]), 7)
        run.assert_called_once_with("x.json")


if __name__ == "__main__":
    unittest.main()
