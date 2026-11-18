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

    def test_part_file_is_unique_exclusive_and_not_the_shared_name(self):
        dest = self.dir / "w.bin"
        seen = []

        class Spy(io.BytesIO):
            headers = {}

            def __enter__(self_):
                seen.extend(p.name for p in self.dir.iterdir())
                return self_

            def __exit__(self_, *a):
                return False

        # A pre-planted symlink at the old shared name must never be written through.
        victim = self.dir / "victim"
        victim.write_bytes(b"keep")
        (self.dir / "w.bin.part").symlink_to(victim)
        assets.verify_or_download("http://x/w", dest, sha(b"abc"), opener=lambda *a, **k: Spy(b"abc"))
        self.assertEqual(victim.read_bytes(), b"keep")
        self.assertEqual(dest.read_bytes(), b"abc")
        parts = [n for n in seen if n.startswith("w.bin.") and n.endswith(".part") and n != "w.bin.part"]
        self.assertEqual(len(parts), 1)
        self.assertEqual([p.name for p in self.dir.glob("w.bin.*.part")], [])

    def test_read_timeout_is_20_seconds(self):
        seen = {}

        def opener(url, timeout):
            seen["t"] = timeout
            return io.BytesIO(b"abc")

        class R(io.BytesIO):
            headers = {}
            __enter__ = lambda s: s
            __exit__ = lambda s, *a: False

        assets.verify_or_download("http://x/w", self.dir / "w.bin", sha(b"abc"), opener=lambda u, timeout: (seen.update(t=timeout), R(b"abc"))[1])
        self.assertEqual(seen["t"], 20)

    def test_sweep_removes_only_old_part_files_in_asset_dirs(self):
        import os, time
        for sub in ("vggish", "models", "other"):
            (self.dir / sub).mkdir()
        old = self.dir / "vggish" / "a.bin.abc123.part"
        fresh = self.dir / "models" / "b.bin.def456.part"
        wrong_name = self.dir / "models" / "notes.part.txt"
        plain = self.dir / "vggish" / "a.bin"
        elsewhere = self.dir / "other" / "x.bin.zzz.part"
        for f in (old, fresh, wrong_name, plain, elsewhere):
            f.write_bytes(b"1")
        stale = time.time() - 7200
        for f in (old, wrong_name, plain, elsewhere):
            os.utime(f, (stale, stale))
        assets.sweep_stale_parts(self.dir)
        self.assertFalse(old.exists())
        for f in (fresh, wrong_name, plain, elsewhere):
            self.assertTrue(f.exists(), f.name)

    def test_verified_cache_keeps_one_entry_per_path_and_is_locked(self):
        sub = self.dir / "sub"
        sub.mkdir()
        target = sub / "a.bin"
        target.write_bytes(b"abc")
        with mock.patch.object(assets, "ASSETS", [self.entry()]):
            assets.asset_status(self.dir)
            target.write_bytes(b"abd")
            assets.asset_status(self.dir)
            target.write_bytes(b"abe")
            assets.asset_status(self.dir)
        self.assertEqual(len([k for k in assets._VERIFIED if k[0] == str(target)]), 1)
        self.assertTrue(hasattr(assets._VERIFIED_LOCK, "acquire"))

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
