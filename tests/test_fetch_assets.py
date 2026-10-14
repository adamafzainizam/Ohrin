import hashlib
import io
import re
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import fetch_openmic, fetch_vggish

ROOT = Path(__file__).resolve().parent.parent


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeResponse(io.BytesIO):
    pass


class VGGishFetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_download_writes_file_when_checksum_matches(self):
        opener = mock.Mock(return_value=FakeResponse(b"abc"))
        dest = self.dir / "sub" / "w.bin"
        state = fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=opener)
        self.assertEqual(state, "downloaded")
        self.assertEqual(dest.read_bytes(), b"abc")
        self.assertFalse(dest.with_name("w.bin.part").exists())

    def test_mismatch_removes_partial_and_raises(self):
        dest = self.dir / "w.bin"
        with self.assertRaises(fetch_vggish.ChecksumMismatch) as ctx:
            fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=lambda u, timeout: FakeResponse(b"zzz"))
        self.assertIn("checksum mismatch", str(ctx.exception))
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("w.bin.part").exists())

    def test_existing_valid_file_is_not_redownloaded(self):
        dest = self.dir / "w.bin"
        dest.write_bytes(b"abc")
        opener = mock.Mock()
        self.assertEqual(fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=opener), "cached")
        opener.assert_not_called()

    def test_existing_invalid_file_requires_force(self):
        dest = self.dir / "w.bin"
        dest.write_bytes(b"old")
        opener = mock.Mock(return_value=FakeResponse(b"abc"))
        with self.assertRaises(fetch_vggish.ExistingFileInvalid):
            fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=opener)
        opener.assert_not_called()
        self.assertEqual(dest.read_bytes(), b"old")
        fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=opener, force=True)
        self.assertEqual(dest.read_bytes(), b"abc")

    def test_constants_match_recorded_checksums(self):
        by_name = {f["name"]: f for f in fetch_vggish.FILES}
        self.assertEqual(by_name["vggish-10086976.pth"]["sha256"],
                         "10086976245803799d9194e9a73d9b6c1549c71d1b80106f5cade5608a561f4b")
        self.assertEqual(by_name["vggish_pca_params.npz"]["sha256"],
                         "4d878af3e306defbfb37095365aa7e5a6cecb86bad13e859e626151c4e6a8b9d")


def make_tgz(path, members):
    with tarfile.open(path, "w:gz") as tf:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


class OpenMICFetchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_openmic_extracts_only_allowlisted_members(self):
        members = {
            "openmic-2018/openmic-2018.npz": b"npz-bytes",
            "openmic-2018/class-map.json": b"{}",
            "openmic-2018/partitions/split01_train.csv": b"a\n",
            "openmic-2018/partitions/split01_test.csv": b"b\n",
            "openmic-2018/audio/000/a.ogg": b"ogg",
            "openmic-2018/._class-map.json": b"apple",
            "evil/../../x": b"x",
            "audio/a.ogg": b"ogg",
        }
        archive = self.dir / "o.tgz"
        make_tgz(archive, members)
        dest = self.dir / "out" / "openmic-2018"
        messages = []
        code = fetch_openmic.fetch_openmic(
            dest, archive=archive, expected_md5=fetch_openmic.file_digest(archive, "md5"),
            expected_npz_sha=sha(b"npz-bytes"), out=messages.append)
        self.assertEqual(code, 0)
        written = sorted(str(p.relative_to(self.dir / "out")) for p in (self.dir / "out").rglob("*") if p.is_file())
        self.assertEqual(written, sorted(f"openmic-2018/{n}" for n in fetch_openmic.ALLOWLIST))
        self.assertFalse((self.dir / "x").exists())
        self.assertTrue(archive.exists())

    def test_openmic_without_yes_does_not_download(self):
        opener = mock.Mock()
        messages = []
        code = fetch_openmic.fetch_openmic(self.dir / "openmic-2018", yes=False, opener=opener, out=messages.append)
        self.assertEqual(code, 0)
        opener.assert_not_called()
        self.assertTrue(any("GB" in m for m in messages))
        self.assertFalse((self.dir / "openmic-2018").exists())

    def test_wrong_npz_checksum_is_an_error(self):
        archive = self.dir / "o.tgz"
        make_tgz(archive, {f"openmic-2018/{n}": b"x" for n in fetch_openmic.ALLOWLIST})
        with self.assertRaises(RuntimeError):
            fetch_openmic.fetch_openmic(self.dir / "d", archive=archive,
                                        expected_md5=fetch_openmic.file_digest(archive, "md5"),
                                        expected_npz_sha=sha(b"other"), out=lambda m: None)

    def test_npz_constant_matches_training_module(self):
        text = (ROOT / "ohrin" / "instruments.py").read_text()
        match = re.search(r'OFFICIAL_NPZ_SHA256 = "([0-9a-f]{64})"', text)
        self.assertEqual(match.group(1), fetch_openmic.NPZ_SHA256)


if __name__ == "__main__":
    unittest.main()
