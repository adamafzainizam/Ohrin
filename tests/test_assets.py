import hashlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ohrin import assets
from scripts import fetch_vggish

ROOT = Path(__file__).resolve().parent.parent
HEAD_SHA = "aa8dc0e5610067fd211393b126cb979ba895d7b61f417a7ddc858cfe2bd70d3d"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Endless(io.BytesIO):
    def read(self, n=-1):
        return b"x" * n

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AssetsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        assets._VERIFIED.clear()
        self.addCleanup(assets._VERIFIED.clear)

    def entry(self, **kw):
        base = {"name": "a.bin", "subdir": "sub", "url": "http://x/a", "sha256": sha(b"abc"), "size": 3, "host": "x"}
        base.update(kw)
        return base

    def test_cancel_removes_partial_and_raises(self):
        dest = self.dir / "w.bin"
        calls = []

        def cancel():
            calls.append(1)
            return len(calls) > 1

        with self.assertRaises(assets.Cancelled):
            assets.verify_or_download("http://x/w", dest, sha(b"x"), opener=lambda *a, **k: Endless(), cancel=cancel)
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("w.bin.part").exists())

    def test_fetch_script_table_matches_assets(self):
        expected = [{k: e[k] for k in ("name", "url", "sha256", "size")} for e in assets.ASSETS if e["subdir"] == "vggish"]
        self.assertEqual(fetch_vggish.FILES, expected)
        self.assertEqual(len(expected), 2)
        self.assertIs(fetch_vggish.verify_or_download, assets.verify_or_download)

    def test_head_entry_checksum_and_size(self):
        head = next(e for e in assets.ASSETS if e["name"] == "openmic-mlp.pt")
        self.assertEqual(head["sha256"], HEAD_SHA)
        self.assertEqual(head["size"], 36805)
        self.assertEqual(head["subdir"], "models")
        self.assertIsNone(head["url"])
        self.assertIsNone(assets.HEAD_URL)

    def test_status_missing_present_unverified_verified(self):
        table = [self.entry(name="m.bin"), self.entry(name="p.bin"), self.entry(name="v.bin")]
        sub = self.dir / "sub"
        sub.mkdir()
        (sub / "p.bin").write_bytes(b"zzzz")
        (sub / "v.bin").write_bytes(b"abc")
        with mock.patch.object(assets, "ASSETS", table):
            status = {s["name"]: s for s in assets.asset_status(self.dir)}
            self.assertEqual(status["m.bin"], {"name": "m.bin", "present": False, "verified": False, "bytes": 0, "size": 3})
            self.assertEqual(status["p.bin"], {"name": "p.bin", "present": True, "verified": False, "bytes": 4, "size": 3})
            self.assertEqual(status["v.bin"], {"name": "v.bin", "present": True, "verified": True, "bytes": 3, "size": 3})
            self.assertEqual([e["name"] for e in assets.missing(self.dir)], ["m.bin", "p.bin"])

    def test_status_caches_verification_by_mtime_and_size(self):
        sub = self.dir / "sub"
        sub.mkdir()
        target = sub / "a.bin"
        target.write_bytes(b"abc")
        with mock.patch.object(assets, "ASSETS", [self.entry()]), \
                mock.patch.object(assets, "sha256_file", wraps=assets.sha256_file) as spy:
            assets.asset_status(self.dir)
            assets.asset_status(self.dir)
            self.assertEqual(spy.call_count, 1)
            target.write_bytes(b"abd")
            status = assets.asset_status(self.dir)
            self.assertEqual(spy.call_count, 2)
            self.assertFalse(status[0]["verified"])

    def test_unpublished_head_makes_download_unavailable_unless_head_present(self):
        head = self.entry(name="h.bin", subdir="models", url=None)
        vgg = self.entry(name="v.bin", subdir="vggish")
        with mock.patch.object(assets, "ASSETS", [vgg, head]):
            self.assertFalse(assets.downloadable(self.dir))
            (self.dir / "models").mkdir()
            (self.dir / "models" / "h.bin").write_bytes(b"abc")
            self.assertTrue(assets.downloadable(self.dir))

    def test_fetch_script_runs_as_plain_script(self):
        result = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_vggish.py"), "--help"],
                                capture_output=True, text=True, cwd=self.dir, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--dest", result.stdout)


if __name__ == "__main__":
    unittest.main()
