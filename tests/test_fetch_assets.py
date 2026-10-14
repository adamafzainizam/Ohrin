import hashlib
import http.client
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


class EndlessResponse:
    """A stream that never ends; counts bytes served and optionally announces a length."""
    def __init__(self, headers=None):
        self.served = 0
        self.headers = headers or {}

    def read(self, n=-1):
        self.served += n
        return b"x" * n

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DyingResponse(io.BytesIO):
    def read(self, n=-1):
        data = super().read(n)
        if not data:
            raise http.client.IncompleteRead(b"")
        return data


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

    def test_oversized_stream_is_aborted_early_and_leaves_nothing(self):
        dest = self.dir / "w.bin"
        stream = EndlessResponse()
        with self.assertRaises(fetch_vggish.SizeExceeded):
            fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), opener=lambda u, timeout: stream,
                                            max_bytes=3 * 1024 * 1024)
        self.assertLessEqual(stream.served, 4 * 1024 * 1024)
        self.assertFalse(dest.exists())
        self.assertFalse(dest.with_name("w.bin.part").exists())

    def test_announced_oversize_is_rejected_before_reading(self):
        stream = EndlessResponse(headers={"Content-Length": "999999999"})
        with self.assertRaises(fetch_vggish.SizeExceeded):
            fetch_vggish.verify_or_download("http://x/w", self.dir / "w.bin", sha(b"abc"),
                                            opener=lambda u, timeout: stream, max_bytes=1000)
        self.assertEqual(stream.served, 0)

    def test_failed_forced_download_keeps_the_existing_file(self):
        dest = self.dir / "w.bin"
        dest.write_bytes(b"old")
        with self.assertRaises(fetch_vggish.ChecksumMismatch):
            fetch_vggish.verify_or_download("http://x/w", dest, sha(b"abc"), force=True,
                                            opener=lambda u, timeout: FakeResponse(b"zzz"))
        self.assertEqual(dest.read_bytes(), b"old")
        self.assertFalse(dest.with_name("w.bin.part").exists())

    def test_main_reports_http_errors_without_traceback(self):
        err = io.StringIO()
        with mock.patch.object(fetch_vggish, "verify_or_download", side_effect=http.client.IncompleteRead(b"x")), \
                mock.patch("sys.stderr", err):
            code = fetch_vggish.main(["--dest", str(self.dir / "v")])
        self.assertEqual(code, 1)
        self.assertIn("error:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_main_reports_oversize_cleanly(self):
        err = io.StringIO()
        with mock.patch.object(fetch_vggish, "verify_or_download", side_effect=fetch_vggish.SizeExceeded("too big")), \
                mock.patch("sys.stderr", err):
            self.assertEqual(fetch_vggish.main(["--dest", str(self.dir / "v")]), 1)
        self.assertIn("too big", err.getvalue())

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

    def _valid_members(self, npz=b"npz-bytes"):
        return {"openmic-2018/openmic-2018.npz": npz, "openmic-2018/class-map.json": b"{}",
                "openmic-2018/partitions/split01_train.csv": b"a\n", "openmic-2018/partitions/split01_test.csv": b"b\n"}

    def test_failed_extraction_leaves_previous_good_files_and_no_staging(self):
        dest = self.dir / "out" / "openmic-2018"
        good = self.dir / "good.tgz"
        make_tgz(good, self._valid_members())
        fetch_openmic.fetch_openmic(dest, archive=good, expected_md5=fetch_openmic.file_digest(good, "md5"),
                                    expected_npz_sha=sha(b"npz-bytes"), out=lambda m: None)
        before = {p.name: p.read_bytes() for p in dest.rglob("*") if p.is_file()}
        bad = self.dir / "bad.tgz"
        make_tgz(bad, self._valid_members(npz=b"tampered"))
        with self.assertRaises(RuntimeError):
            fetch_openmic.fetch_openmic(dest, archive=bad, expected_md5=fetch_openmic.file_digest(bad, "md5"),
                                        expected_npz_sha=sha(b"npz-bytes"), out=lambda m: None)
        self.assertEqual({p.name: p.read_bytes() for p in dest.rglob("*") if p.is_file()}, before)
        self.assertEqual([p.name for p in dest.parent.iterdir()], ["openmic-2018"])

    def test_checksum_failure_on_fresh_destination_creates_nothing_final(self):
        archive = self.dir / "o.tgz"
        make_tgz(archive, self._valid_members())
        out_dir = self.dir / "fresh"
        with self.assertRaises(RuntimeError):
            fetch_openmic.fetch_openmic(out_dir / "openmic-2018", archive=archive,
                                        expected_md5=fetch_openmic.file_digest(archive, "md5"),
                                        expected_npz_sha=sha(b"other"), out=lambda m: None)
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_missing_member_is_an_error_and_cleans_up(self):
        archive = self.dir / "o.tgz"
        members = self._valid_members(); del members["openmic-2018/class-map.json"]
        make_tgz(archive, members)
        with self.assertRaises(RuntimeError):
            fetch_openmic.fetch_openmic(self.dir / "out" / "d", archive=archive,
                                        expected_md5=fetch_openmic.file_digest(archive, "md5"),
                                        expected_npz_sha=sha(b"npz-bytes"), out=lambda m: None)
        self.assertEqual(list((self.dir / "out").iterdir()), [])

    def test_symlinked_partitions_directory_is_refused(self):
        archive = self.dir / "o.tgz"
        make_tgz(archive, self._valid_members())
        elsewhere = self.dir / "elsewhere"; elsewhere.mkdir()
        dest = self.dir / "out" / "d"; dest.mkdir(parents=True)
        (dest / "partitions").symlink_to(elsewhere)
        with self.assertRaises(RuntimeError):
            fetch_openmic.fetch_openmic(dest, archive=archive, expected_md5=fetch_openmic.file_digest(archive, "md5"),
                                        expected_npz_sha=sha(b"npz-bytes"), out=lambda m: None)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_oversized_download_is_aborted_early(self):
        stream = EndlessResponse()
        target = self.dir / "o.tgz"
        with self.assertRaises(RuntimeError):
            fetch_openmic.download("http://x", target, "0" * 32, opener=lambda u, timeout: stream,
                                   max_bytes=2 * 1024 * 1024)
        self.assertLessEqual(stream.served, 3 * 1024 * 1024)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_name("o.tgz.part").exists())

    def test_main_turns_tar_and_http_errors_into_friendly_exit_codes(self):
        garbage = self.dir / "g.tgz"
        garbage.write_bytes(b"this is not a tar archive")
        md5 = fetch_openmic.file_digest(garbage, "md5")
        with self.assertRaises(tarfile.TarError):
            fetch_openmic.fetch_openmic(self.dir / "d", archive=garbage, expected_md5=md5, out=lambda m: None)
        for exc in (tarfile.ReadError("bad"), http.client.IncompleteRead(b"x"), EOFError("truncated")):
            err = io.StringIO()
            with mock.patch.object(fetch_openmic, "fetch_openmic", side_effect=exc), mock.patch("sys.stderr", err):
                self.assertEqual(fetch_openmic.main(["--dest", str(self.dir / "d")]), 1)
            self.assertIn("error:", err.getvalue())

    def test_npz_constant_matches_training_module(self):
        text = (ROOT / "ohrin" / "instruments.py").read_text()
        match = re.search(r'OFFICIAL_NPZ_SHA256 = "([0-9a-f]{64})"', text)
        self.assertEqual(match.group(1), fetch_openmic.NPZ_SHA256)


if __name__ == "__main__":
    unittest.main()
