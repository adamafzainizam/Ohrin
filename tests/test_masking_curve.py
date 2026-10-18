import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

HAS_TORCH = importlib.util.find_spec("torch") is not None
if HAS_TORCH:
    import torch
    from experiments import masking_curve as mc

PARITY_OK = {"ok": True, "reasons": [], "gaps": []}


def fake_labels():
    """Hand-made OpenMIC-like labels, TARGETS = guitar, bass, drums, voice; all annotated; 30 test tracks + train."""
    rows = []  # (key, guitar, bass, drums) Y_true
    for track in range(100, 130):
        rows.append((f"{track:06d}_0", .9, .1, .9))      # drums target (guitar present)
        rows.append((f"{track:06d}_1", .1, .1, .1))      # neutral
        rows.append((f"{track:06d}_2", .9, .9, .1))      # bass target; also a masker for drums
        rows.append((f"{track:06d}_3", .9, .1, .1))      # guitar masker for either class
    for track in range(200, 210):
        rows.append((f"{track:06d}_0", .9, .1, .9))      # train partition
        rows.append((f"{track:06d}_2", .9, .9, .1))
    return labels_from(rows)


def labels_from(rows, extra_unannotated=0):
    keys = np.array([r[0] for r in rows])
    true = np.array([[r[1], r[2], r[3], .5] for r in rows], dtype=float)
    mask = np.ones_like(true, dtype=bool)
    test = np.flatnonzero(np.array([int(k[:6]) < 200 for k in keys]))
    return keys, true, mask, test


def relaxed_labels():
    """fake_labels plus test clips with guitar present but bass/drums not annotated."""
    keys, true, mask, test = fake_labels()
    extra = [f"{t:06d}_4" for t in range(100, 130)] + [f"{t:06d}_4" for t in range(200, 205)]
    keys = np.r_[keys, extra]
    true = np.r_[true, np.tile([.9, .5, .5, .5], (len(extra), 1))]
    new_mask = np.ones((len(extra), 4), dtype=bool)
    new_mask[:, 1:3] = False
    mask = np.r_[mask, new_mask]
    test = np.flatnonzero(np.array([int(k[:6]) < 200 for k in keys]))
    return keys, true, mask, test


def ogg(size=64):
    return b"OggS" + b"\0" * (size - 4)


def tar_with(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            else:
                info.type = kind
                info.linkname = "x"
                tar.addfile(info)


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class PairTests(unittest.TestCase):
    def test_pairs_stay_in_test_partition_and_obey_label_rules(self):
        keys, true, mask, test = fake_labels()
        index = {k: i for i, k in enumerate(keys)}
        for target, col in (("drums", 2), ("bass", 1)):
            pairs, avail = mc.build_pairs(keys, true, mask, test, target, n_pairs=20)
            self.assertEqual(len(pairs), avail["pairs_built"])
            for p in pairs:
                for k in (p.target, p.masker, p.neutral):
                    self.assertIn(index[k], set(test))
                self.assertGreaterEqual(true[index[p.target], col], .5)
                self.assertLessEqual(true[index[p.masker], col], .2)
                self.assertTrue(true[index[p.masker], 0] >= .5 or true[index[p.masker], 1] >= .5)
                n = index[p.neutral]
                self.assertLessEqual(max(true[n, 0], true[n, 1], true[n, col]), .2)  # drums-absent neutrals only
                self.assertNotEqual(mc.track_of(p.target), mc.track_of(p.masker))
                self.assertNotEqual(mc.track_of(p.target), mc.track_of(p.neutral))

    def test_bass_target_masker_is_guitar(self):
        keys, true, mask, test = fake_labels()
        pairs, _ = mc.build_pairs(keys, true, mask, test, "bass", n_pairs=5)
        index = {k: i for i, k in enumerate(keys)}
        self.assertTrue(pairs and all(true[index[p.masker], 0] >= .5 and true[index[p.masker], 1] <= .2 for p in pairs))

    def test_seed_reproduces_and_changes(self):
        keys, true, mask, test = fake_labels()
        a, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=15)
        b, _ = mc.build_pairs(keys[::-1], true[::-1], mask[::-1], len(keys) - 1 - test, "drums", n_pairs=15)
        c, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=15, seed=7)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_reuse_cap_and_availability_report(self):
        keys, true, mask, test = fake_labels()
        pairs, avail = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=300)
        self.assertEqual(avail["targets_available"], 30)
        self.assertEqual(avail["pairs_requested"], 300)
        self.assertEqual(len(pairs), 30)
        for field in ("masker", "neutral"):
            counts = {}
            for p in pairs:
                counts[getattr(p, field)] = counts.get(getattr(p, field), 0) + 1
            self.assertLessEqual(max(counts.values()), 3)

    def test_slots_are_spent_only_on_kept_pairs(self):
        keys, true, mask, test = fake_labels()
        pairs, avail = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=5, max_reuse=1)
        self.assertEqual(len(pairs), 5)
        self.assertEqual(len({p.masker for p in pairs}), 5)
        self.assertEqual(avail["pairs_with_neutral"], 5)

    def test_drums_neutral_pool_needs_target_absent_clips(self):
        keys, true, mask, test = fake_labels()
        mask = mask.copy()
        for i, k in enumerate(keys):
            if k.endswith("_1") and int(k[:6]) >= 104:
                mask[i, 2] = False  # drums not annotated: still guitar/bass-free, but not drums-absent
        pairs, avail = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=10)
        self.assertEqual(avail["neutral_clips_available"], 30)
        self.assertEqual(avail["neutral_target_absent_available"], 4)
        self.assertLess(avail["neutral_target_absent_available"], mc.MIN_NEUTRAL_CLIPS)
        self.assertEqual(avail["neutral_arm"], "unavailable")
        self.assertTrue(pairs)  # pairs do not need a neutral clip
        self.assertTrue(all(p.neutral is None for p in pairs))

    def test_target_clips_never_serve_as_maskers_or_neutrals(self):
        keys, true, mask, test = fake_labels()
        drum_targets = mc.target_candidates(keys, true, mask, test, "drums")
        pairs, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=300)
        self.assertFalse({p.masker for p in pairs} & drum_targets)
        self.assertFalse({p.neutral for p in pairs} & drum_targets)
        extra = {p.masker for p in pairs}
        again, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=300, exclude=extra)
        self.assertFalse({p.masker for p in again} & extra)

    def test_all_pairs_allow_a_clip_to_serve_both_classes_but_not_its_own(self):
        keys, true, mask, test = fake_labels()
        data = {"keys": keys, "true": true, "mask": mask, "test_indices": test}
        both = mc.all_pairs(data)
        for cls, other in (("drums", "bass"), ("bass", "drums")):
            own = mc.target_candidates(keys, true, mask, test, cls)
            self.assertFalse({p.masker for p in both[cls][0]} & own)
        bass_targets = mc.target_candidates(keys, true, mask, test, "bass")
        self.assertTrue({p.masker for p in both["drums"][0]} & bass_targets)  # no cross-class exclusion


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class RelaxedPairTests(unittest.TestCase):
    def test_relaxed_adds_unannotated_maskers_and_strict_does_not(self):
        keys, true, mask, test = relaxed_labels()
        _, strict = mc.build_pairs(keys, true, mask, test, "drums", mode="strict")
        pairs, relaxed = mc.build_pairs(keys, true, mask, test, "drums", mode="relaxed")
        self.assertEqual(relaxed["mode"], "relaxed")
        self.assertEqual(relaxed["real_maskers_available"], strict["real_maskers_available"] + 30)
        index = {k: i for i, k in enumerate(keys)}
        for p in pairs:
            row = index[p.masker]
            self.assertTrue(not mask[row, 2] or true[row, 2] <= .2)
            self.assertTrue(true[row, 0] >= .5 or (mask[row, 1] and true[row, 1] >= .5))

    def test_relaxed_determinism_partition_and_reuse(self):
        keys, true, mask, test = relaxed_labels()
        a, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=25, mode="relaxed")
        b, _ = mc.build_pairs(keys[::-1], true[::-1], mask[::-1], len(keys) - 1 - test, "drums", n_pairs=25, mode="relaxed")
        self.assertEqual(a, b)
        index = {k: i for i, k in enumerate(keys)}
        counts = {}
        for p in a:
            for k in (p.target, p.masker, p.neutral):
                self.assertIn(index[k], set(test))
                self.assertLess(int(k[:6]), 200)
            self.assertNotEqual(mc.track_of(p.target), mc.track_of(p.masker))
            counts[p.masker] = counts.get(p.masker, 0) + 1
        self.assertLessEqual(max(counts.values()), 3)

    def test_unknown_mode_rejected(self):
        keys, true, mask, test = fake_labels()
        with self.assertRaises(ValueError):
            mc.build_pairs(keys, true, mask, test, "drums", mode="loose")


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class MixingTests(unittest.TestCase):
    def sines(self):
        t = np.arange(16000) / 16000
        target = .5 * np.sin(2 * np.pi * 1000 * t) + 3 * np.sin(2 * np.pi * 50 * t)       # loud 50 Hz is out of band
        masker = 2 * np.sin(2 * np.pi * 2000 * t) + 6 * np.sin(2 * np.pi * 7900 * t)      # loud 7.9 kHz is out of band
        return target, masker

    def test_ratio_is_measured_in_band_and_checked_independently(self):
        target, masker = self.sines()
        for ratio in mc.RATIOS:
            mix, achieved, fullband = mc.mix_at_ratio(target, masker, ratio)
            amp = 2 * np.abs(np.fft.rfft(mix)) / len(mix)  # 1 Hz bins: read the two in-band tones directly
            self.assertAlmostEqual(20 * np.log10(amp[2000] / amp[1000]), ratio, delta=.1)
            self.assertAlmostEqual(achieved, ratio, delta=.1)
            self.assertGreater(abs(fullband - ratio), 1.0)  # full-band RMS would have been the wrong ratio
            self.assertAlmostEqual(20 * np.log10(np.abs(mix).max()), -1.0, places=6)

    def test_length_and_silence(self):
        rng = np.random.default_rng(0)
        mix, _, _ = mc.mix_at_ratio(rng.standard_normal(16000) * .2, rng.standard_normal(12000) * 3, 6)
        self.assertEqual(len(mix), 12000)
        with self.assertRaises(ValueError):
            mc.mix_at_ratio(np.zeros(100), np.ones(100), 0)
        with self.assertRaises(ValueError):  # energy only below the band counts as silent
            t = np.arange(16000) / 16000
            mc.mix_at_ratio(np.sin(2 * np.pi * 50 * t), np.sin(2 * np.pi * 1000 * t), 0)

    def test_pink_noise_is_band_limited_unit_rms_and_falls_with_frequency(self):
        noise = mc.pink_noise(2 ** 16, np.random.default_rng(1))
        self.assertAlmostEqual(mc.rms(noise), 1.0, places=6)
        self.assertAlmostEqual(mc.band_rms(noise), 1.0, places=3)
        freqs = np.fft.rfftfreq(len(noise), 1 / 16000)
        power = np.abs(np.fft.rfft(noise)) ** 2
        self.assertLess(power[(freqs < 100) | (freqs > 7600)].sum(), 1e-6 * power.sum())
        low, high = power[(freqs > 150) & (freqs < 250)].mean(), power[(freqs > 3000) & (freqs < 3100)].mean()
        self.assertGreater(low, 5 * high)


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class ExtractionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.tgz = self.root / "a.tgz"
        self.audio = self.root / "audio_out"

    def extract(self, keys, members=None, **kw):
        if members is not None:
            tar_with(self.tgz, members)
        return mc.extract_clips(self.tgz, self.audio, keys, expected_md5=kw.pop("expected_md5", md5(self.tgz)), **kw)

    def test_extracts_only_wanted_clips_to_validated_paths(self):
        out = self.extract(["000178_3840", "000999_1"], [
            ("openmic-2018/audio/000/000178_3840.ogg", ogg(), "file"),
            ("openmic-2018/audio/000/000308_61440.ogg", ogg(), "file"),
            ("openmic-2018/audio/000/000311_0.ogg", ogg(), "file"),
            ("openmic-2018/openmic-2018.npz", b"x" * 10, "file")])
        self.assertEqual((out["extracted"], out["missing"]), (1, ["000999_1"]))
        self.assertEqual(sorted(p.name for p in self.audio.rglob("*") if p.is_file()), ["000178_3840.ogg"])
        again = mc.extract_clips(self.tgz, self.audio, ["000178_3840"], expected_md5="0" * 32)  # nothing to do: not even hashed
        self.assertEqual(again["already_present"], 1)

    def test_dot_slash_prefix_member_is_accepted(self):
        out = self.extract(["000178_3840"], [("./openmic-2018/audio/000/000178_3840.ogg", ogg(), "file")])
        self.assertEqual(out["extracted"], 1)

    def test_rejects_hostile_members(self):
        for name, kind in (("/etc/x", "file"), ("openmic-2018/audio/../../x.ogg", "file"),
                           ("openmic-2018/audio/000/000178_3840.ogg", tarfile.SYMTYPE),
                           ("openmic-2018/audio/000/000178_3840.ogg", tarfile.LNKTYPE),
                           ("dev", tarfile.CHRTYPE), ("fifo", tarfile.FIFOTYPE), ("a\\b", "file")):
            with self.subTest(name=name, kind=kind):
                with self.assertRaises(mc.UnsafeArchive):
                    self.extract(["000178_3840"], [(name, ogg(), kind)])
        self.assertFalse(self.audio.exists() and any(self.audio.rglob("*.ogg")))

    def test_skips_names_outside_the_pattern_and_bad_content_leaves_no_tmp(self):
        out = self.extract(["000178_3840"], [("openmic-2018/audio/001/000178_3840.ogg", ogg(), "file"),
                                             ("other/audio/000/000178_3840.ogg", ogg(), "file"),
                                             ("openmic-2018/audio/000/000178_3840.wav", ogg(), "file")])
        self.assertEqual(out["missing"], ["000178_3840"])
        with self.assertRaises(mc.UnsafeArchive):
            self.extract(["000178_3840"], [("openmic-2018/audio/000/000178_3840.ogg", b"RIFF" + b"\0" * 60, "file")])
        self.assertFalse(list(self.audio.rglob("*.tmp")))

    def test_size_cap(self):
        with self.assertRaises(mc.UnsafeArchive):
            self.extract(["000178_3840"], [("openmic-2018/audio/000/000178_3840.ogg", ogg(mc.MAX_MEMBER_BYTES + 1), "file")])

    def test_unfinished_download_missing_archive_and_checksum(self):
        with self.assertRaises(FileNotFoundError):
            mc.extract_clips(self.tgz, self.audio, ["000178_3840"])
        tar_with(self.tgz, [("openmic-2018/audio/000/000178_3840.ogg", ogg(), "file")])
        with self.assertRaisesRegex(mc.ArchiveCorrupt, "checksum"):
            mc.extract_clips(self.tgz, self.audio, ["000178_3840"], expected_md5="0" * 32)
        Path(str(self.tgz) + ".part").write_bytes(b"not a tar")
        with self.assertRaisesRegex(mc.DownloadUnfinished, "not finished"):
            mc.extract_clips(self.tgz, self.audio, ["000178_3840"])

    def test_truncated_archive_is_reported_and_cleaned_up(self):
        tar_with(self.tgz, [("openmic-2018/audio/000/000178_3840.ogg", ogg(200000), "file"),
                            ("openmic-2018/audio/000/000308_61440.ogg", ogg(200000), "file")])
        data = self.tgz.read_bytes()
        self.tgz.write_bytes(data[:len(data) // 2])
        with self.assertRaises(mc.ArchiveCorrupt):
            self.extract(["000308_61440"])
        self.assertFalse(list(self.audio.rglob("*.tmp")))

    def test_stops_after_last_wanted_member(self):
        out = self.extract(["000178_3840"], [("openmic-2018/audio/000/000178_3840.ogg", ogg(), "file"),
                                             ("/absolute/after", b"", "file")])
        self.assertEqual(out["extracted"], 1)  # never reaches the hostile tail

    def test_symlinked_clip_directory_destination_and_existing_target_refused(self):
        tar_with(self.tgz, [("openmic-2018/audio/000/000178_3840.ogg", ogg(), "file")])
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        self.audio.mkdir()
        (self.audio / "000").symlink_to(elsewhere)
        with self.assertRaisesRegex(mc.UnsafeArchive, "symlink"):
            self.extract(["000178_3840"])
        self.assertFalse(list(elsewhere.iterdir()))
        (self.audio / "000").unlink()
        link = self.root / "link_out"
        link.symlink_to(self.audio)
        with self.assertRaisesRegex(mc.UnsafeArchive, "symlink"):
            mc.extract_clips(self.tgz, link, ["000178_3840"], expected_md5=md5(self.tgz))
        (self.audio / "000").mkdir()
        (self.audio / "000" / "000178_3840.ogg").mkdir()  # exists but not a regular file
        with self.assertRaisesRegex(mc.UnsafeArchive, "regular"):
            self.extract(["000178_3840"])

    def test_extract_command_exit_codes(self):
        keys, true, mask, test = fake_labels()
        data = {"keys": keys, "true": true, "mask": mask, "test_indices": test}
        wanted = mc.parity_clip_keys(data)
        with mock.patch.object(mc, "load_labels", return_value=data):
            root = self.root / "data"
            (root / "cache").mkdir(parents=True)

            def code():
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    return mc.extract_command(root), out.getvalue()

            self.assertEqual(code()[0], mc.EXIT_ARCHIVE_ABSENT)
            Path(root / "cache/openmic-2018-v1.0.0.tgz.part").write_bytes(b"x")
            self.assertEqual(code()[0], mc.EXIT_DOWNLOAD_UNFINISHED)
            Path(root / "cache/openmic-2018-v1.0.0.tgz.part").unlink()
            tar_with(root / mc.ARCHIVE, [(f"openmic-2018/audio/{wanted[0][:3]}/{wanted[0]}.ogg", ogg(), "file")])
            self.assertEqual(code()[0], mc.EXIT_CORRUPT)  # the real MD5 constant does not match this fake archive
            real = mc.extract_clips
            with mock.patch.object(mc, "extract_clips", lambda a, r, k: real(a, r, k, expected_md5=md5(a))):
                status, text = code()
            self.assertEqual(status, mc.EXIT_MISSING_CLIPS)  # the archive holds one of the requested clips
            self.assertIn("not found in the archive", text)


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class ScoringTests(unittest.TestCase):
    class FakeModel:
        def __init__(self, n_examples):
            self.n = n_examples
            self.seen = []

        def embed(self, samples):
            return np.full((self.n, 128), 255, dtype=np.uint8)

        def _probabilities(self, x):
            self.seen.append(x)
            return np.tile(np.linspace(.1, .4, 4), (len(x), 1))

    def test_blocks_follow_the_app(self):
        model = self.FakeModel(23)
        out = mc.score_samples(model, np.zeros(10))
        self.assertEqual(model.seen[0].shape, (3, 128))
        np.testing.assert_allclose(out, np.linspace(.1, .4, 4))
        model = self.FakeModel(11)
        mc.score_samples(model, np.zeros(10))
        self.assertEqual(model.seen[0].shape, (1, 128))
        model = self.FakeModel(2)
        mc.score_samples(model, np.zeros(10))
        self.assertEqual(model.seen[0].shape, (1, 128))
        with self.assertRaises(ValueError):
            mc.score_samples(self.FakeModel(0), np.zeros(10))

    def test_parity_compares_all_four_classes(self):
        names = ["guitar", "bass", "drums", "voice"]
        stored = np.array([[.5, .20, .70, .5], [.5, .29, .55, .5]])
        fresh = np.array([[.5, .22, .70, .9], [.5, .31, .90, .5]])
        thr = np.array([.4, .3, .6, .4])
        rows = mc.parity_rows(["a", "b"], stored, fresh, thr, names)
        self.assertEqual(len(rows), 8)
        by = {(r["key"], r["class"]): r for r in rows}
        self.assertFalse(by[("a", "voice")]["pass"])        # diff .4 only visible when voice is compared
        self.assertTrue(by[("a", "bass")]["pass"])
        self.assertFalse(by[("b", "drums")]["pass"])
        self.assertFalse(by[("b", "bass")]["same_decision"])
        summary = mc.parity_summary(rows)
        self.assertEqual((summary["rows_failing"], summary["pass"], summary["n_clips"]), (3, False, 2))
        self.assertIn("DIFFERS", mc.format_parity(rows))
        with self.assertRaises(ValueError):
            mc.parity_rows(["a"], stored[:1], fresh[:1], thr, names[:2])

    def test_parity_gate_reasons(self):
        good = {"pass": True, "n_clips": 200}
        self.assertTrue(mc.parity_gate(good, good)["ok"])
        self.assertIn("audio_subset missing", mc.parity_gate(None, good)["reasons"])
        out_of_tol = mc.parity_gate({"pass": False, "n_clips": 8}, {"pass": False, "n_clips": 200})
        self.assertTrue(out_of_tol["ok"])  # a gap is reported, not a blocker
        self.assertEqual(len(out_of_tol["gaps"]), 2)
        short = mc.parity_gate(good, {"pass": True, "n_clips": 84})
        self.assertFalse(short["ok"])
        self.assertTrue(any("fewer than 200" in r for r in short["reasons"]))
        self.assertFalse(mc.parity_gate(good, None)["ok"])


class FakeEmbedModel:
    """Level-dependent fake VGGish + head so raw and peak-normalised audio score differently."""
    classes = ["guitar", "bass", "drums", "voice"]
    thresholds = np.array([.5, .5, .5, .5])

    def __init__(self):
        torch.manual_seed(0)
        self.head = torch.nn.Linear(128, 4)

    def embed(self, samples):
        return np.full((10, 128), int(min(255, np.abs(samples).mean() * 300)), dtype=np.uint8)

    def _probabilities(self, x):
        with torch.no_grad():
            return torch.sigmoid(self.head(torch.as_tensor(x.astype(np.float32) / 255.0))).numpy()


def fake_decode(path):
    stem = Path(path).stem
    rng = np.random.default_rng(int(stem[:6]) * 10 + int(stem.split("_")[1]))
    t = np.arange(32000) / 16000
    return .1 * np.sin(2 * np.pi * rng.uniform(300, 3000) * t) + .05 * rng.standard_normal(len(t))


def hand_scores(n, real=None, pink=None, clip=None, alone=0.0):
    """Scores where >= .5 is a detection. real/pink/clip: {ratio: value or array}.

    The default is 90% detected (every tenth pair, offset 1, missed) so no control sits at the 0.98 ceiling."""
    real, pink, clip = real or {}, pink or {}, clip or {}
    base = np.where(np.arange(n) % 10 == 1, 0.0, 1.0)
    arr = lambda v: np.full(n, v, dtype=float) if np.isscalar(v) else np.asarray(v, dtype=float)
    return {"unmixed": np.ones(n), "alone_real": np.full(n, alone), "alone_pink": np.zeros(n), "alone_neutral_clip": np.zeros(n),
            "real": {r: arr(real.get(r, base)) for r in mc.RATIOS},
            "pink": {r: arr(pink.get(r, base)) for r in mc.RATIOS},
            "neutral_clip": {r: arr(clip.get(r, base)) for r in mc.RATIOS}}


def hand_ids(n=40, n_maskers=10, n_neutrals=10, neutral=True):
    return {"target": np.array([f"{i // 2:06d}_{i % 2}" for i in range(n)]),
            "masker": np.array([f"m{i % n_maskers}" for i in range(n)]),
            "neutral": np.array([f"n{i % n_neutrals}" if neutral else "" for i in range(n)])}


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class GateTests(unittest.TestCase):
    def verdict(self, scores, ids=None, parity=PARITY_OK, n_boot=200):
        ids = ids or hand_ids()
        if not np.isfinite(scores["neutral_clip"][0]).all():
            ids = hand_ids(neutral=False)
        return mc.summarise(scores, ids, .5, parity, n_boot)["gate0"]

    def test_continue_needs_both_controls(self):
        g = self.verdict(hand_scores(40, real={12: 0.0, 18: 0.0}))
        self.assertEqual(g["verdict"], "continue")
        self.assertEqual(g["conclusion"], mc.CONCLUSION)
        self.assertFalse(g["provisional"])
        for arm in g["arms"].values():
            self.assertAlmostEqual(arm["excess_drop"]["value"], .9)
            self.assertGreater(arm["excess_drop"]["ci95"][0], 0)
        self.assertEqual(g["pooling_note"], mc.POOLING_NOTE)
        self.assertIsNotNone(g["composite_info_only"])

    def test_controls_disagree(self):
        g = self.verdict(hand_scores(40, real={12: 0.0}, pink={12: 0.0}))  # pink drops as much, neutral clips do not
        self.assertEqual(g["verdict"], "controls_disagree")
        self.assertIn("controls disagree", g["conclusion"])
        self.assertTrue(g["arms"]["neutral_clips"]["passes"])
        self.assertFalse(g["arms"]["pink_noise"]["passes"])

    def test_negative_verdicts_never_state_the_claim(self):
        cases = [self.verdict(hand_scores(40, real={12: 0.0}, pink={12: 0.0}, clip={12: 0.0})),
                 self.verdict(hand_scores(40, real={12: 0.0}, pink={12: 0.0})),
                 self.verdict(hand_scores(40, real={12: 0.0}), parity={"ok": False, "reasons": ["x"]}),
                 self.verdict({**hand_scores(40), "unmixed": np.zeros(40)})]
        self.assertEqual([c["verdict"] for c in cases],
                         ["stop_no_excess", "controls_disagree", "inconclusive_parity", "stop_baseline"])
        for c in cases:
            self.assertNotIn(mc.CONCLUSION, c["conclusion"])
            self.assertTrue(c["conclusion"].startswith(("Not shown:", "Not established:")))

    def test_margin_below_point_one_stops(self):
        real = np.where(np.arange(40) % 10 == 1, 0.0, 1.0)
        real[[2, 3, 4]] = 0
        g = self.verdict(hand_scores(40, real={12: real}))
        self.assertEqual(g["verdict"], "stop_no_excess")
        self.assertAlmostEqual(g["arms"]["pink_noise"]["excess_drop"]["value"], .075)

    def test_neutral_arm_unavailable_makes_pink_only_provisional(self):
        scores = hand_scores(40, real={12: 0.0})
        scores["neutral_clip"] = {r: np.full(40, np.nan) for r in mc.RATIOS}
        scores["alone_neutral_clip"] = np.full(40, np.nan)
        g = self.verdict(scores)
        self.assertEqual(g["verdict"], "pink_only_excess")
        self.assertTrue(g["provisional"])
        self.assertEqual(g["provisional_reason"], "provisional (pink-noise control only)")
        self.assertEqual(g["arms"]["neutral_clips"]["status"], "unavailable")

    def test_fewer_than_five_neutral_clips_is_unavailable(self):
        g = mc.summarise(hand_scores(40, real={12: 0.0}), hand_ids(n_neutrals=4), .5, PARITY_OK, 100)["gate0"]
        self.assertFalse(g["arms"]["neutral_clips"]["available"])
        self.assertTrue(g["provisional"])

    def test_only_a_floor_control_is_excluded_a_ceiling_control_still_counts(self):
        floor = {r: 0.0 for r in mc.RATIOS}
        g = self.verdict(hand_scores(40, real={12: 0.0}, pink=floor))
        pink = g["arms"]["pink_noise"]
        self.assertEqual((pink["saturation"], pink["flag"], pink["informative"]), ("saturated_low", "control saturated", False))
        self.assertIsNone(pink["passes"])
        self.assertTrue(g["provisional"])
        self.assertEqual(g["provisional_reason"], "provisional (neutral-clip control only)")
        ceiling = self.verdict(hand_scores(40, real={12: 0.0}, pink={r: 1.0 for r in mc.RATIOS}))["arms"]["pink_noise"]
        self.assertEqual((ceiling["saturation"], ceiling["flag"], ceiling["informative"]), ("saturated_high", "control saturated", True))
        self.assertTrue(ceiling["passes"])  # a ceiling control that does not degrade is valid evidence

    def test_a_failing_ceiling_control_vetoes_instead_of_vanishing(self):
        pink = {r: 1.0 for r in mc.RATIOS}
        pink[12] = 0.0  # starts at the ceiling and drops as much as the real arm
        g = self.verdict(hand_scores(40, real={12: 0.0}, pink=pink))
        self.assertEqual(g["arms"]["pink_noise"]["saturation"], "saturated_high")
        self.assertFalse(g["arms"]["pink_noise"]["passes"])
        self.assertEqual(g["verdict"], "controls_disagree")
        self.assertNotEqual(g["conclusion"], mc.CONCLUSION)

    def test_ceiling_boundary_is_at_exactly_point_nine_eight(self):
        real_drop, factors = np.ones(100), [np.arange(100), np.arange(100)]
        zero = np.zeros(100)
        at = mc.control_arm("x", real_drop, zero, factors, 0.98, 50)
        below = mc.control_arm("x", real_drop, zero, factors, 0.979, 50)
        self.assertEqual((at["saturation"], below["saturation"]), ("saturated_high", "ok"))
        self.assertTrue(at["informative"] and below["informative"])

    def test_controls_that_miss_different_pairs_still_pass(self):
        clip = np.where(np.arange(40) % 10 == 3, 0.0, 1.0)  # neutral misses other pairs than pink does
        g = self.verdict(hand_scores(40, real={12: 0.0, 18: 0.0}, clip={r: clip for r in mc.RATIOS}))
        self.assertEqual(g["verdict"], "continue")

    def test_one_target_track_or_masker_cannot_pass(self):
        ids = hand_ids(n_maskers=1)
        ids["target"] = np.array(["000000_0"] * 40)
        g = mc.summarise(hand_scores(40, real={12: 0.0}), ids, .5, PARITY_OK, 100)["gate0"]
        self.assertEqual(g["verdict"], "too_few_sources")
        self.assertNotEqual(g["verdict"], "continue")

    def test_no_pairs_is_stated_not_called_a_baseline_failure(self):
        empty = {"target": np.array([]), "masker": np.array([]), "neutral": np.array([])}
        g = mc.gate0({}, empty, PARITY_OK)
        self.assertEqual(g["verdict"], "no_pairs")

    def test_parity_gap_with_unmeasured_flips_is_provisional(self):
        gap = {"ok": True, "reasons": [], "gaps": ["test-clip parity outside tolerance"]}
        g = mc.summarise(hand_scores(40, real={12: 0.0, 18: 0.0}), hand_ids(), .5, gap, 100, None)["gate0"]
        self.assertTrue(g["provisional"])
        self.assertIn("not measured", g["provisional_reason"])
        clean = mc.summarise(hand_scores(40, real={12: 0.0, 18: 0.0}), hand_ids(), .5, PARITY_OK, 100, None)["gate0"]
        self.assertFalse(clean["provisional"])

    def test_pink_arm_reports_a_matched_actual_snr_sensitivity_outside_the_verdict(self):
        g = self.verdict(hand_scores(40, real={12: 0.0, 18: 0.0}))
        sens = g["arms"]["pink_noise"]["sensitivity_matched_actual_snr_0_to_6"]
        self.assertEqual(set(sens), {"value", "ci95"})
        self.assertEqual(g["verdict"], "continue")

    def test_pink_only_conclusion_states_the_offset(self):
        scores = hand_scores(40, real={12: 0.0})
        scores["neutral_clip"] = {r: np.full(40, np.nan) for r in mc.RATIOS}
        scores["alone_neutral_clip"] = np.full(40, np.nan)
        g = self.verdict(scores)
        self.assertIn("12 dB below the nominal ratio", g["conclusion"])
        self.assertIn("12 dB below nominal", g["rule"])

    def test_pink_only_excess_never_states_the_neutral_claim(self):
        scores = hand_scores(40, real={12: 0.0})
        scores["neutral_clip"] = {r: np.full(40, np.nan) for r in mc.RATIOS}
        scores["alone_neutral_clip"] = np.full(40, np.nan)
        g = self.verdict(scores)
        self.assertEqual(g["verdict"], "pink_only_excess")
        self.assertNotIn(mc.CONCLUSION, g["conclusion"])
        self.assertTrue(g["provisional"])

    def test_decision_flips_above_five_percent_make_the_verdict_provisional(self):
        scores = hand_scores(40, real={12: 0.0, 18: 0.0})
        few = mc.summarise(scores, hand_ids(), .5, PARITY_OK, 100, {"flips": 2, "n": 54})["gate0"]
        many = mc.summarise(scores, hand_ids(), .5, PARITY_OK, 100, {"flips": 3, "n": 54})["gate0"]
        self.assertFalse(few["provisional"])
        self.assertTrue(many["provisional"])
        self.assertIn("3 of 54", many["provisional_reason"])

    def test_one_odd_masker_widens_the_crossed_ci(self):
        ids = hand_ids(n_maskers=5, n_neutrals=10)
        real12 = np.where(ids["masker"] == "m0", 0.0, np.where(np.arange(40) % 10 == 1, 0.0, 1.0))  # one masker of five drives the whole drop
        scores = hand_scores(40, real={12: real12})
        g = mc.summarise(scores, ids, .5, PARITY_OK, 400)["gate0"]
        arm = g["arms"]["pink_noise"]
        self.assertAlmostEqual(arm["excess_drop"]["value"], .2)
        self.assertLessEqual(arm["excess_drop"]["ci95"][0], 0)
        self.assertEqual(g["verdict"], "stop_no_excess")
        tracks = np.array([mc.track_of(k) for k in ids["target"]])
        excess = scores["real"][0] - scores["real"][12]
        track_only = mc.stat(excess, [tracks], 400)
        self.assertGreater(track_only["ci95"][0], arm["excess_drop"]["ci95"][0])  # tracks-only would have been too narrow
        loo = arm["leave_one_out"]["masker"]
        self.assertAlmostEqual(loo["min"], 0.0)
        self.assertEqual(loo["min_without"], "m0")
        self.assertFalse(loo["all_at_least_margin"])

    def test_bootstrap_weights_are_products_of_resampling_counts(self):
        w = mc.bootstrap_weights([np.array(["a", "a", "b", "b"]), np.array(["x", "y", "x", "y"])], 50, seed=1)
        self.assertEqual(w.shape, (50, 4))
        np.testing.assert_array_equal(w, np.round(w))
        self.assertTrue((w[:, 0] + w[:, 1]).max() <= 4 * 2)
        a1 = mc.bootstrap_weights([np.array(["a", "b"])], 5, seed=3)
        np.testing.assert_array_equal(a1, mc.bootstrap_weights([np.array(["a", "b"])], 5, seed=3))

    def test_auc_and_leave_one_out(self):
        self.assertEqual(mc.auc([1, 2], [0, 0]), 1.0)
        self.assertEqual(mc.auc([1], [1]), .5)
        self.assertTrue(np.isnan(mc.auc([], [1])))
        out = mc.leave_one_out([1, 1, 0, 0], ["a", "a", "b", "b"])
        self.assertEqual((out["min"], out["min_without"], out["max"], out["max_without"]), (0.0, "a", 1.0, "b"))

    def test_summary_counts_distinct_masker_alone_and_recall(self):
        scores = hand_scores(40, real={12: 0.0, 18: 0.0})
        scores["alone_real"] = np.where(np.isin(hand_ids()["masker"], ["m0", "m1"]), 1.0, 0.0)  # 2 of 10 maskers fire
        out = mc.summarise(scores, hand_ids(), .5, PARITY_OK, 100)
        self.assertEqual((out["n_pairs"], out["distinct_targets"], out["distinct_maskers"], out["distinct_neutrals"]),
                         (40, 40, 10, 10))
        self.assertAlmostEqual(out["masker_alone_rate"]["real"]["value"], .2)
        self.assertEqual(out["masker_alone_rate"]["real"]["n_distinct_clips"], 10)
        self.assertAlmostEqual(out["recall"]["real"][12]["value"], 0.0)
        self.assertAlmostEqual(out["continuous"]["auc_mixed_vs_masker_alone"]["pink"][0], .95)  # 10% of pink scores tie at 0
        self.assertNotIn("masker_alone_fpr", out)
        json.dumps(mc.to_jsonable(out), allow_nan=False)

    def test_threshold_is_inclusive_and_nan_survives(self):
        hits = mc.hits_from_scores({"a": np.array([.5, .49, np.nan])}, .5)
        np.testing.assert_array_equal(hits["a"][:2], [1., 0.])
        self.assertTrue(np.isnan(hits["a"][2]))


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class CollectTests(unittest.TestCase):
    def test_dry_run_on_synthetic_audio(self):
        keys, true, mask, test = fake_labels()
        pairs, _ = mc.build_pairs(keys, true, mask, test, "drums", n_pairs=6)
        rng = np.random.default_rng(0)
        clips = {k: rng.standard_normal(16000 * 2) * .1 for k in mc.pair_keys(pairs)}
        calls = []

        def score(samples):
            calls.append(len(samples))
            self.assertAlmostEqual(20 * np.log10(np.abs(samples).max()), -1.0, places=5)
            return np.array([0, 0, min(1.0, float(np.sqrt(np.mean(samples ** 2))) * 10), 0])

        cache = {}
        scores, kept, info = mc.collect_scores(pairs, 2, clips.__getitem__, score, cache=cache)
        self.assertEqual(len(kept), 6)
        self.assertLess(info["max_band_ratio_error_db"], .1)
        self.assertEqual(set(scores["pink"]), set(mc.RATIOS))
        n_first = len(calls)
        mc.collect_scores(pairs, 2, clips.__getitem__, score, cache=cache)
        self.assertLess(len(calls) - n_first, n_first)
        out = mc.summarise(scores, mc.ids_from_pairs(kept), .5, PARITY_OK, 20)
        self.assertEqual(out["n_pairs"], 6)

    def test_rows_stay_aligned_with_kept_pairs_when_a_middle_pair_is_skipped(self):
        pairs = [mc.Pair(f"{100 + i:06d}_0", f"{200 + i:06d}_3", None) for i in range(4)]
        level = {p.target: .05 * (i + 1) for i, p in enumerate(pairs)}
        rng = np.random.default_rng(0)
        clips = {}
        for p in pairs:
            clips[p.target] = rng.standard_normal(8000) * level[p.target]
            clips[p.masker] = rng.standard_normal(8000) * .1
        clips[pairs[1].masker] = np.zeros(8000)  # skip the second pair
        scores, kept, info = mc.collect_scores(
            pairs, 2, clips.__getitem__, lambda s: np.array([0, 0, float(np.sqrt(np.mean(s ** 2))), 0]))
        self.assertEqual([p.target for p in kept], [pairs[0].target, pairs[2].target, pairs[3].target])
        self.assertEqual(info["pairs_skipped_silent"], 1)
        self.assertEqual(info["unmixed_probs"].shape, (3, 4))
        for arr in (scores["unmixed"], scores["alone_real"], scores["real"][0], scores["pink"][0], scores["neutral_clip"][0]):
            self.assertEqual(len(arr), 3)
        self.assertTrue(np.isnan(scores["neutral_clip"][0]).all())  # no neutral clips: NaN, not zero
        np.testing.assert_allclose(scores["unmixed"], [info["unmixed_probs"][i][2] for i in range(3)])


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class WiringTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        keys, true, mask, test = fake_labels()
        self.model = FakeEmbedModel()
        raw = np.array([self.model.embed(fake_decode(Path(f"{k}.ogg")))[0].astype(float) / 255 for k in keys])
        self.data = {"keys": keys, "true": true, "mask": mask, "test_indices": test, "features": raw.astype(np.float32)}
        for k in mc.parity_clip_keys(self.data, 5):
            path = mc.audio_path(self.root / "audio", k)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ogg())
        sub = self.root / "audio_subset" / "audio" / "100"
        sub.mkdir(parents=True)
        (sub / "000100_0.ogg").write_bytes(ogg())

    def run_mode(self, mode, data=None, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return mc.run(self.root, None, self.root / "out", n_boot=20, decode=fake_decode, pairs_mode=mode,
                          model=self.model, data=data or self.data, parity_min_clips=5, **kw)

    def test_strict_run_uses_raw_clips_for_parity_and_reports_the_gap(self):
        doc = self.run_mode("strict")
        block = doc["strict"]
        self.assertTrue(block["parity"]["ok"], block["parity"]["reasons"])
        self.assertTrue(block["parity"]["clips"]["summary"]["pass"])
        gap = block["classes"]["drums"]["unmixed_recall_gap"]
        self.assertEqual(set(gap), {"stored_features", "reextracted_raw", "peak_normalised"})
        self.assertAlmostEqual(gap["stored_features"], gap["reextracted_raw"])
        self.assertIn(block["classes"]["drums"]["gate0"]["verdict"], {"continue", "pink_only_excess", "stop_no_excess", "controls_disagree", "stop_baseline", "no_informative_control"})
        self.assertEqual(block["classes"]["bass"]["gate0"]["pooling_note"], mc.POOLING_NOTE)
        written = json.loads((self.root / "out" / "masking_curve.json").read_text())
        self.assertEqual(written["schema"], 3)
        self.assertIn("timestamp_utc", written["strict"]["metadata"])
        self.assertEqual(written["strict"]["metadata"]["constants"]["seed"], mc.SEED)

    def test_stored_feature_mismatch_is_reported_as_a_gap_not_a_blocker(self):
        bad = dict(self.data, features=np.clip(self.data["features"] + .5, 0, 1).astype(np.float32))
        doc = self.run_mode("strict", data=bad)
        parity = doc["strict"]["parity"]
        self.assertTrue(parity["ok"])
        self.assertTrue(parity["gaps"])
        self.assertIn("GAPS", mc.format_summary(doc["strict"]))
        self.assertIn("target decision flips", mc.format_summary(doc["strict"]))
        self.assertIn("all_classes_summary", parity["clips"])
        for res in doc["strict"]["classes"].values():
            self.assertNotEqual(res["gate0"]["verdict"], "inconclusive_parity")

    def test_too_few_clips_for_parity_is_inconclusive(self):
        for k in mc.parity_clip_keys(self.data, 10 ** 6):
            path = mc.audio_path(self.root / "audio", k)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(ogg())
        with contextlib.redirect_stdout(io.StringIO()):
            doc = mc.run(self.root, None, self.root / "out", n_boot=10, decode=fake_decode, model=self.model,
                         data=self.data, parity_min_clips=10 ** 6)
        self.assertEqual(doc["strict"]["classes"]["drums"]["gate0"]["verdict"], "inconclusive_parity")

    def test_each_mode_is_written_fresh_and_the_other_block_is_kept(self):
        out = self.root / "out"
        out.mkdir()
        (out / "masking_curve.json").write_text(json.dumps({"stale_key": 1, "classes": {"x": 1}}))
        self.run_mode("strict")
        first = json.loads((out / "masking_curve.json").read_text())
        self.assertEqual(set(first), {"schema", "strict"})  # legacy keys dropped
        relaxed = self.run_mode("relaxed")
        self.assertEqual(set(relaxed), {"schema", "strict", "relaxed"})
        self.assertEqual(relaxed["relaxed"]["caveat"], mc.RELAXED_CAVEAT)
        self.assertIn("masker_predicted_target", relaxed["relaxed"]["classes"]["drums"])
        again = self.run_mode("strict")
        self.assertIn("relaxed", again)
        (out / "masking_curve.json").write_text(json.dumps({"schema": 2, "relaxed": {"old": "rules"}}))
        self.assertNotIn("relaxed", self.run_mode("strict"))  # blocks from older rules are never mixed in
        self.run_mode("relaxed")
        again = self.run_mode("strict")
        self.assertNotIn("stale_key", again)

    def test_missing_extracted_clip_is_a_clear_error(self):
        mc.audio_path(self.root / "audio", mc.parity_clip_keys(self.data, 5)[0]).unlink()
        with self.assertRaisesRegex(FileNotFoundError, "extract"):
            self.run_mode("strict")


@unittest.skipUnless(HAS_TORCH, "PyTorch not installed")
class RemainingFixesTests(unittest.TestCase):
    def test_masker_alone_rate_is_the_mean_over_crop_outcomes_and_order_independent(self):
        values = np.array([1.0, 0.0, 1.0, 1.0])
        ids = np.array(["a", "a", "b", "c"])
        means, names = mc.mean_per_id(values, ids)
        self.assertEqual(list(names), ["a", "b", "c"])
        np.testing.assert_allclose(means, [.5, 1.0, 1.0])
        swapped, _ = mc.mean_per_id(values[[1, 0, 2, 3]], ids)
        np.testing.assert_allclose(swapped, means)  # exchanging a clip's two crop outcomes changes nothing

    def test_neutral_clip_identity_widens_the_real_arm_ci_because_it_sets_the_crop(self):
        n = 40
        ids = hand_ids(n, n_maskers=10, n_neutrals=2)
        by_neutral = np.where(ids["neutral"] == "n0", 0.0, 1.0)  # outcome fixed by which neutral clip cropped the pair
        out = mc.summarise(hand_scores(n, real={0: by_neutral, 12: by_neutral}), ids, .5, PARITY_OK, 300)
        low, high = out["recall"]["real"][0]["ci95"]
        self.assertGreater(high - low, .8)

    def test_pink_is_mixed_at_the_preregistered_offset_with_several_draws(self):
        seen = []
        pairs = [mc.Pair("000100_0", "000200_3", None)]
        rng = np.random.default_rng(0)
        clips = {"000100_0": rng.standard_normal(16000 * 2) * .1, "000200_3": rng.standard_normal(16000 * 2) * .1}
        scores, kept, info = mc.collect_scores(pairs, 2, clips.__getitem__, lambda s: np.array([0, 0, .5, 0]), ratios=(0,))
        self.assertEqual(scores["pink"][0].shape, (1, mc.NOISE_DRAWS))
        self.assertEqual(scores["alone_pink"].shape, (1, mc.NOISE_DRAWS))
        self.assertLess(info["max_band_ratio_error_db"], .1)  # achieved ratio matches ratio + offset

    def test_progress_callback_fires_every_25_pairs(self):
        pairs = [mc.Pair(f"{100 + i:06d}_0", f"{200 + i:06d}_3", None) for i in range(60)]
        rng = np.random.default_rng(0)
        clips = {k: rng.standard_normal(4000) * .1 for k in mc.pair_keys(pairs)}
        seen = []
        mc.collect_scores(pairs, 2, clips.__getitem__, lambda s: np.array([0, 0, .5, 0]), ratios=(0,),
                          progress=lambda done, total: seen.append((done, total)))
        self.assertEqual(seen, [(25, 60), (50, 60)])

    def test_pink_offset_value_and_summary_collapses_draws(self):
        self.assertEqual(mc.PINK_OFFSET_DB, -12.0)
        n = 6
        scores = hand_scores(n)
        scores["alone_pink"] = np.zeros((n, mc.NOISE_DRAWS))
        scores["pink"] = {r: np.ones((n, mc.NOISE_DRAWS)) for r in mc.RATIOS}
        scores["pink"][12][:, 0] = 0.0  # one of three draws loses the target at +12
        out = mc.summarise(scores, hand_ids(n, 3, 3), .5, PARITY_OK, 20)
        self.assertAlmostEqual(out["recall"]["pink"][12]["value"], 2 / 3)
        self.assertEqual(out["pink_noise_draw_recall"][12], [0.0, 1.0, 1.0])
        self.assertTrue(out["under_powered_vs_n"])
        self.assertEqual(out["distinct_masker_tracks"], 3)

    def test_all_conditions_use_the_same_cropped_interval(self):
        lengths = []
        pairs = [mc.Pair("000100_0", "000200_3", "000300_1")]
        rng = np.random.default_rng(1)
        clips = {"000100_0": rng.standard_normal(16000 * 3) * .1, "000200_3": rng.standard_normal(16000 * 2) * .1,
                 "000300_1": rng.standard_normal(16000 * 4) * .1}

        def score(samples):
            lengths.append(len(samples))
            return np.array([0, 0, .5, 0])

        mc.collect_scores(pairs, 2, clips.__getitem__, score, ratios=(0,))
        self.assertEqual(set(lengths), {16000 * 2})

    def test_extraction_uses_an_unpredictable_exclusive_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "a.tgz"
            member = root / "src" / "audio" / "000" / "000100_0.ogg"
            member.parent.mkdir(parents=True)
            member.write_bytes(ogg())
            with tarfile.open(archive, "w:gz") as tar:
                tar.add(root / "src" / "audio", arcname="audio")
            digest = mc.file_md5(archive)
            predictable = root / "out" / "000" / "000100_0.ogg.tmp"
            predictable.parent.mkdir(parents=True)
            target = root / "secret.txt"
            target.write_text("keep")
            predictable.symlink_to(target)  # the old fixed name would have been written through this link
            mc.extract_clips(archive, root / "out", {"000100_0"}, expected_md5=digest)
            self.assertEqual(target.read_text(), "keep")
            self.assertTrue(mc.audio_path(root / "out", "000100_0").is_file())


class LazyTorchTests(unittest.TestCase):
    def test_import_does_not_load_torch(self):
        code = "import sys; import experiments.masking_curve; sys.exit(1 if 'torch' in sys.modules else 0)"
        result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
