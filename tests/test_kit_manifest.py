import contextlib
import copy
import hashlib
import csv
import io
import os
import stat
import struct
import sys
import tempfile
import textwrap
import unittest
import wave
from unittest import mock
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from experiments import kit_manifest as km

ARCHIVE = 'TEST.rar'


def unrar_block(path, size=1000, crc='AAAA0001', typ='File'):
    return (f'        Name: {path}\n        Type: {typ}\n        Size: {size}\n'
            f' Packed size: 500\n       Ratio: 50%\n     mtime: 2024-01-01 00:00:00,000000000\n'
            f'  Attributes: ..A....\n       CRC32: {crc}\n     Host OS: Windows\n'
            f' Compression: RAR 5.0(v50) -m3 -md=32M\n\n')


def unrar_text(blocks):
    return ('\nUNRAR 7.00 freeware      Copyright (c) 1993-2024 Alexander Roshal\n\n'
            'Archive: /x/TEST.rar\nDetails: RAR 5\n\n' + ''.join(blocks))


def sevenz_block(path, size=1000, crc='AAAA0001', folder='-', attrs='A', extra=''):
    return (f'Path = {path}\nFolder = {folder}\nSize = {size}\nPacked Size = 500\n'
            f'Modified = 2024-01-01 00:00:00.0000000\nCreated = \nAccessed = \n'
            f'Attributes = {attrs}\nAlternate Stream = -\nEncrypted = -\nCRC = {crc}\n'
            f'Method = LZMA2\n{extra}\n')


def sevenz_text(blocks):
    return ('7-Zip 23.01 (x64)\n\nListing archive: /x/TEST.rar\n\n--\nPath = /x/TEST.rar\n'
            'Type = Rar5\nPhysical Size = 5\n\n----------\n' + ''.join(blocks))


# (path, size, crc, kind) records shared by both listing formats
RECORDS = [
    ('Root/Kit A', 0, '', 'dir'),
    ('Root/Kit A/Kick 1.wav', 1000, '00000001', 'file'),
    ('Root/Kit A/Snare 1.wav', 1100, '00000002', 'file'),
    ('Root/Kit A/Hat 1.WAV', 1200, '00000003', 'file'),
    ('Root/Kit A/notes.txt', 50, '00000004', 'file'),
    ('Root/Kit A/preset.fxp', 60, '00000005', 'file'),
    ('Root/Kit A/Bass.mp3', 70, '00000006', 'file'),
    ('Root/Kit A/pattern.mid', 80, '00000007', 'file'),
    ('Root/Kit A/Melody LOOP 120.wav', 900, '00000008', 'file'),
    ('Root/Kit A/Loops/plain.wav', 901, '00000009', 'file'),
    ('Root/Kit A/extra.zip', 90, '0000000A', 'file'),
    ('Root/Kit A/odd.xyz', 91, '0000000B', 'file'),
    ('Root/Kit B/808 low.wav', 1300, '00000010', 'file'),
    ('Root/Kit C - Twin/one.wav', 1400, '00000020', 'file'),
    ('Root/Kit C - Twin (1)/one copy.wav', 1400, '00000020', 'file'),
    ('Root/Top level file.wav', 500, '00000030', 'file'),
    ('Root/Kit D/link.wav', 5, '00000040', 'link'),
    ('Root/Kit E/empty.wav', 0, '00000000', 'file'),
]


def render(fmt):
    blocks = []
    for path, size, crc, kind in RECORDS:
        if fmt == 'unrar':
            typ = {'dir': 'Directory', 'file': 'File', 'link': 'Unix symbolic link'}[kind]
            blocks.append(unrar_block(path, size, crc, typ))
        else:
            blocks.append(sevenz_block(path, size, crc, '+' if kind == 'dir' else '-',
                                       attrs='D' if kind == 'dir' else 'A',
                                       extra='Symbolic Link = target\n' if kind == 'link' else ''))
    return unrar_text(blocks) if fmt == 'unrar' else sevenz_text(blocks)


def as_text(rows):
    """Rows as they read back from CSV (everything a string, bools as true/false)."""
    return [{k: ('true' if v is True else 'false' if v is False else str(v)) for k, v in r.items()}
            for r in rows]


def by_path(rows):
    return {r['archive_path']: r for r in rows}


class ListingParserTests(unittest.TestCase):
    def test_both_formats_agree(self):
        a = km.parse_listing(render('unrar'))
        b = km.parse_listing(render('7z'))
        self.assertEqual(len(a), len(RECORDS))
        self.assertEqual(a, b)
        self.assertEqual(a[1], {'path': 'Root/Kit A/Kick 1.wav', 'kind': 'file', 'size': 1000,
                                'crc32': '00000001', 'symlink': False})
        self.assertEqual(a[0]['kind'], 'dir')
        self.assertTrue(a[16]['symlink'])

    def test_unknown_listing_rejected(self):
        with self.assertRaises(km.ManifestError):
            km.parse_listing('nothing useful here')

    def test_7z_header_block_not_an_entry(self):
        self.assertNotIn('/x/TEST.rar', [e['path'] for e in km.parse_listing(render('7z'))])


class ManifestRuleTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.recon = km.build_rows(km.parse_listing(render('unrar')), ARCHIVE)
        self.r = by_path(self.rows)

    def test_schema_columns_and_file_id(self):
        row = self.r['Root/Kit A/Kick 1.wav']
        self.assertEqual(set(row), set(km.FIELDS))
        self.assertEqual(row['file_id'], km.file_id(ARCHIVE, 'Root/Kit A/Kick 1.wav'))
        self.assertEqual(len(row['file_id']), 64)
        self.assertEqual(row['kit'], 'Kit A')
        self.assertEqual(row['ext'], 'wav')
        self.assertEqual(row['acquisition_status'], 'unverified')
        self.assertEqual(row['provenance_flag'], 'none_stated')
        self.assertIs(row['include'], True)
        self.assertEqual(row['reason'], '')

    def test_directories_are_not_rows(self):
        self.assertNotIn('Root/Kit A', self.r)
        self.assertEqual(self.recon['directories'], 1)

    def test_non_wav_extensions_excluded(self):
        for name in ('notes.txt', 'preset.fxp', 'Bass.mp3', 'pattern.mid', 'extra.zip', 'odd.xyz'):
            row = self.r['Root/Kit A/' + name]
            self.assertFalse(row['include'], name)
            self.assertTrue(row['reason'], name)
        self.assertEqual(self.r['Root/Kit A/Bass.mp3']['reason'], 'non_wav_extension')

    def test_uppercase_wav_extension_included(self):
        self.assertTrue(self.r['Root/Kit A/Hat 1.WAV']['include'])
        self.assertEqual(self.r['Root/Kit A/Hat 1.WAV']['ext'], 'wav')

    def test_loop_matching(self):
        self.assertEqual(self.r['Root/Kit A/Melody LOOP 120.wav']['reason'], 'loop_path')
        self.assertEqual(self.r['Root/Kit A/Loops/plain.wav']['reason'], 'loop_path')
        self.assertTrue(km.has_loop('a/LoOpS/b.wav'))
        self.assertFalse(km.has_loop('a/kick.wav'))

    def test_recycled_kits_excluded_and_flagged(self):
        entries = [
            {'path': 'R/Alpha Maker - Gamma Pack/a.wav', 'kind': 'file', 'size': 10, 'crc32': '00000001',
             'symlink': False},
            {'path': 'R/- Beta Maker - Chronicles Of The Delta Pack (Sound Kit)/b.wav',
             'kind': 'file', 'size': 11, 'crc32': '00000002', 'symlink': False},
            {'path': 'R/Other Kit/c.wav', 'kind': 'file', 'size': 12, 'crc32': '00000003',
             'symlink': False},
        ]
        recycled = (('alpha maker', 'gamma pack'), ('beta maker', 'delta pack'))
        rows, _ = km.build_rows(entries, ARCHIVE, None, recycled)
        self.assertEqual([r['reason'] for r in rows], ['recycled_or_collated_kit'] * 2 + [''])
        self.assertEqual([r['provenance_flag'] for r in rows],
                         ['recycled_or_collated'] * 2 + ['none_stated'])

    def test_twin_copies_collapse(self):
        first = self.r['Root/Kit C - Twin/one.wav']
        second = self.r['Root/Kit C - Twin (1)/one copy.wav']
        self.assertTrue(first['include'])
        self.assertFalse(second['include'])
        self.assertEqual(second['reason'], 'duplicate_crc32_size')
        self.assertEqual(second['duplicate_of'], first['file_id'])

    def test_same_crc_different_size_not_duplicate(self):
        entries = [{'path': f'R/K/{i}.wav', 'kind': 'file', 'size': 10 + i, 'crc32': 'AA000000',
                    'symlink': False} for i in range(2)]
        rows, _ = km.build_rows(entries, ARCHIVE)
        self.assertTrue(all(r['include'] for r in rows))

    def test_symlink_and_empty_excluded(self):
        self.assertEqual(self.r['Root/Kit D/link.wav']['reason'], 'symlink_entry')
        self.assertEqual(self.r['Root/Kit E/empty.wav']['reason'], 'empty_or_unsized')

    def test_top_level_file_has_empty_kit(self):
        self.assertEqual(self.r['Root/Top level file.wav']['kit'], '')

    def test_every_excluded_row_has_reason(self):
        self.assertTrue(all(r['reason'] for r in self.rows if not r['include']))

    def test_missing_reason_fails(self):
        self.r['Root/Kit A/notes.txt']['reason'] = ''
        with self.assertRaises(km.ManifestError):
            km.validate_rows(self.rows)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(km.ManifestError):
                km.write_manifest(self.rows, Path(tmp) / 'm.csv', base=tmp)

    def test_unclassified_entries_reported(self):
        listed = dict(self.recon['unclassified'])
        self.assertEqual(listed['Root/Kit A/extra.zip'], 'nested_archive')
        self.assertEqual(listed['Root/Kit A/odd.xyz'], 'unknown_extension:xyz')
        self.assertEqual(listed['Root/Kit D/link.wav'], 'symlink_entry')
        self.assertNotIn('Root/Kit A/notes.txt', listed)
        self.assertNotIn('Root/Kit A/Bass.mp3', listed)
        text = km.format_reconciliation(self.recon, verbose=True)
        self.assertIn('Root/Kit A/odd.xyz', text)
        self.assertIn('non_wav_extension', text)

    def test_reconciliation_counts_add_up(self):
        rc = self.recon
        self.assertEqual(rc['entries'], len(RECORDS))
        self.assertEqual(rc['directories'] + rc['files'], rc['entries'])
        self.assertEqual(rc['included'] + sum(rc['excluded_by_reason'].values()), rc['files'])

    def test_both_formats_give_same_manifest(self):
        other, _ = km.build_rows(km.parse_listing(render('7z')), ARCHIVE)
        self.assertEqual(self.rows, other)

    def test_acquisition_mapping(self):
        rows, _ = km.build_rows(km.parse_listing(render('unrar')), ARCHIVE, {'Kit A': 'purchased'})
        got = by_path(rows)
        self.assertEqual(got['Root/Kit A/Kick 1.wav']['acquisition_status'], 'purchased')
        self.assertEqual(got['Root/Kit B/808 low.wav']['acquisition_status'], 'unverified')
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'a.csv'
            f.write_text('kit,status\nKit A,Purchased\nKit B,free\n')
            self.assertEqual(km.read_acquisition(f), {'kit a': 'purchased', 'kit b': 'free'})
            f.write_text('Kit A,bought\n')
            with self.assertRaises(km.ManifestError):
                km.read_acquisition(f)

    def test_name_label_is_weak_hint(self):
        self.assertEqual(km.name_label('R/K/Kick 1.wav'), 'kick')
        self.assertEqual(km.name_label('R/K/Snare.wav'), 'snare')
        self.assertEqual(km.name_label('R/K/Open Hat.wav'), 'hat')
        self.assertEqual(km.name_label('R/K/808 low.wav'), 'bass')
        self.assertEqual(km.name_label('R/Hats/x.wav'), 'hat')
        self.assertEqual(km.name_label('R/K/Pluck.wav'), 'other')
        self.assertEqual(km.name_label('R/K/zzz.wav'), 'none')

    def test_csv_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = km.write_manifest(self.rows, Path(tmp) / 'sub' / 'm.csv', base=tmp)
            with open(path, newline='') as fh:
                self.assertEqual(next(csv.reader(fh)), km.FIELDS)
            self.assertEqual(as_text(km.read_manifest(path)), as_text(self.rows))


class PathGuardTests(unittest.TestCase):
    def test_manifest_refused_outside_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(km.ManifestError):
                km.write_manifest([], Path(tmp) / 'm.csv')  # default base is data/private
        with self.assertRaises(km.ManifestError):
            km.require_under(km.ROOT / 'outputs' / 'm.csv', km.PRIVATE_ROOT, 'x')
        with self.assertRaises(km.ManifestError):
            km.require_under(km.PRIVATE_ROOT / '..' / 'm.csv', km.PRIVATE_ROOT, 'x')
        km.require_under(km.PRIVATE_ROOT / 'kits' / 'm.csv', km.PRIVATE_ROOT, 'x')

    def test_symlink_escape_refused(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as other:
            base = Path(tmp) / 'private'
            base.mkdir()
            (base / 'link').symlink_to(other)
            with self.assertRaises(km.ManifestError):
                km.require_under(base / 'link' / 'm.csv', base, 'x')

    def test_cli_refuses_outside_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            listing = Path(tmp) / 'l.txt'
            listing.write_text(render('unrar'))
            self.assertEqual(km.main(['manifest', '--listing', str(listing), '--archive-name', ARCHIVE,
                                      '--no-recycled-kits', str(Path(tmp) / 'm.csv')]), 2)
            self.assertFalse((Path(tmp) / 'm.csv').exists())

    def test_member_path_guard(self):
        km.check_member_path('Root/Kit/a b (1).wav')
        for bad in ('/etc/passwd', 'a/../../b.wav', '../x.wav', 'C:/x.wav', 'a\\b.wav', '',
                    'a/*.wav', 'a/b?.wav', 'a\x00b.wav'):
            with self.assertRaises(km.ManifestError, msg=repr(bad)):
                km.check_member_path(bad)

    def test_plan_rejects_unsafe_included_rows(self):
        rows, _ = km.build_rows(km.parse_listing(render('unrar')), ARCHIVE)
        self.assertEqual(len(km.plan_extraction(rows)), sum(r['include'] for r in rows))
        for bad in ('/abs/x.wav', 'Root/../../x.wav'):
            rows[1]['archive_path'] = bad
            with self.assertRaises(km.ManifestError):
                km.plan_extraction(rows)

    def test_outdir_must_be_fresh_under_kits(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'kits'
            base.mkdir()
            with self.assertRaises(km.ManifestError):
                km.prepare_outdir(Path(tmp) / 'elsewhere', base)
            with self.assertRaises(km.ManifestError):
                km.prepare_outdir(base, base)
            (base / 'empty').mkdir()
            with self.assertRaises(km.ManifestError):  # even an empty existing dir: exclusive create
                km.prepare_outdir(base / 'empty', base)
            km.prepare_outdir(base / 'new', base)
            self.assertTrue((base / 'new').is_dir())
            with self.assertRaises(km.ManifestError):
                km.prepare_outdir(base / 'new', base)

    def test_verify_rejects_symlink_and_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'out'
            (out / 'R').mkdir(parents=True)
            target = Path(tmp) / 'secret.wav'
            target.write_bytes(b'x' * 10)
            (out / 'R' / 'a.wav').symlink_to(target)
            row = {'archive_path': 'R/a.wav', 'listing_size': '10', 'listing_crc32': ''}
            self.assertEqual(km.verify_extracted(out, row), 'extract_not_regular_file')
            (out / 'R' / 'b.wav').write_bytes(b'abc')
            self.assertEqual(km.verify_extracted(out, dict(row, archive_path='R/b.wav')),
                             'extract_size_mismatch')
            self.assertEqual(km.verify_extracted(
                out, dict(row, archive_path='R/b.wav', listing_size='3', listing_crc32='00000000')),
                'extract_crc_mismatch')
            self.assertEqual(km.verify_extracted(out, dict(row, archive_path='R/none.wav')),
                             'extract_missing')
            crc = km.crc32_of(out / 'R' / 'b.wav')
            self.assertEqual(km.verify_extracted(
                out, dict(row, archive_path='R/b.wav', listing_size='3', listing_crc32=crc)), '')


def write_wav(path, samples, rate=16000, width=2, channels=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b''.join(struct.pack('<h', int(s)) for s in samples))


@unittest.skipIf(np is None, 'numpy not installed')
class MeasurementTests(unittest.TestCase):
    def test_tone_centroid_and_duration(self):
        rate = 16000
        t = np.arange(rate) / rate
        x = np.sin(2 * np.pi * 1000 * t)
        dur, onsets, centroid = km.signal_measures(x, rate)
        self.assertAlmostEqual(dur, 1.0)
        self.assertAlmostEqual(centroid, 1000, delta=20)

    def test_clicks_counted_as_onsets(self):
        rate = 16000
        x = np.zeros(rate * 2)
        for start in (1000, 9000, 20000):
            x[start:start + 400] = np.hanning(400) * 0.8
        _, onsets, _ = km.signal_measures(x, rate)
        self.assertEqual(onsets, 3)

    def test_silence_has_no_onsets_or_centroid(self):
        self.assertEqual(km.signal_measures(np.zeros(4000), 16000), (0.25, 0, None))

    def test_decode_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = (Path(tmp) / n for n in ('a.wav', 'b.wav', 'c.wav'))
            write_wav(a, [0, 1000, -1000, 500])
            write_wav(b, [0, 1000, -1000, 500])
            write_wav(c, [0, 1000, -1000, 501])
            xa, rate, ha = km.decode_wav(a)
            self.assertEqual(rate, 16000)
            self.assertEqual(len(xa), 4)
            self.assertEqual(ha, km.decode_wav(b)[2])
            self.assertNotEqual(ha, km.decode_wav(c)[2])

    def test_stereo_mixdown_and_24bit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 's.wav'
            write_wav(p, [1000, -1000, 2000, 0], channels=2)
            x, _, _ = km.decode_wav(p)
            np.testing.assert_allclose(x, [0, 1000 / 32768])
            q = Path(tmp) / 'w24.wav'
            with wave.open(str(q), 'wb') as w:
                w.setnchannels(1)
                w.setsampwidth(3)
                w.setframerate(8000)
                w.writeframes((0x400000).to_bytes(3, 'little') + (-0x400000 & 0xFFFFFF).to_bytes(3, 'little'))
            x, _, _ = km.decode_wav(q)
            np.testing.assert_allclose(x, [0.5, -0.5])




# ---------- hardening ----------

def unrar_with_comment(blocks, comment):
    return ('\nUNRAR 7.00 freeware      Copyright (c) 1993-2024 Alexander Roshal\n\n'
            'Archive: /x/TEST.rar\n' + comment + 'Details: RAR 5\n\n' + ''.join(blocks))


_crc_counter = iter(range(1, 10 ** 6))


def entry(path, size=10, crc=None, kind='file', symlink=False):
    crc = '%08X' % next(_crc_counter) if crc is None else crc
    return {'path': path, 'kind': kind, 'size': size, 'crc32': crc, 'symlink': symlink}


class ListingHardeningTests(unittest.TestCase):
    def test_comment_before_details_cannot_forge_entries(self):
        comment = '        Name: forged.wav\n        Type: File\n----------\nPath = forged2.wav\n'
        text = unrar_with_comment([unrar_block('Root/K/real.wav')], comment)
        self.assertEqual([e['path'] for e in km.parse_listing(text)], ['Root/K/real.wav'])

    def test_name_with_newline_is_kept_whole_and_unsafe(self):
        for text in (unrar_text([unrar_block('Root/K/a\nb.wav')]),
                     sevenz_text([sevenz_block('Root/K/a\nb.wav')])):
            entries = km.parse_listing(text)
            self.assertEqual([e['path'] for e in entries], ['Root/K/a\nb.wav'])
            rows, _ = km.build_rows(entries, ARCHIVE)
            self.assertEqual(rows[0]['reason'], 'unsafe_name')

    def test_forged_record_inside_name_is_rejected(self):
        forged = 'Root/K/x.wav\n        Type: File\n        Size: 1\n        CRC32: 00000001'
        with self.assertRaises(km.ManifestError):
            km.parse_listing(unrar_text([unrar_block(forged)]))
        forged7 = 'Root/K/x.wav\nFolder = -\nSize = 1\nCRC = 00000001'
        with self.assertRaises(km.ManifestError):
            km.parse_listing(sevenz_text([sevenz_block(forged7)]))

    def test_garbage_between_records_rejected(self):
        with self.assertRaises(km.ManifestError):
            km.parse_listing(unrar_text([unrar_block('Root/K/a.wav'), 'stray line\n']))
        with self.assertRaises(km.ManifestError):
            km.parse_listing(sevenz_text([sevenz_block('Root/K/a.wav'), 'stray line\n']))

    def test_trailing_whitespace_in_name_is_preserved_then_unsafe(self):
        entries = km.parse_listing(unrar_text([unrar_block('Root/K/a.wav ')]))
        self.assertEqual(entries[0]['path'], 'Root/K/a.wav ')
        rows, _ = km.build_rows(entries, ARCHIVE)
        self.assertEqual(rows[0]['reason'], 'unsafe_name')

    def test_7z_symlink_attribute_strings(self):
        for attrs, extra in (('A lrwxrwxrwx', ''), ('', 'Copy Link = other\n')):
            e = km.parse_listing(sevenz_text([sevenz_block('Root/K/l.wav', attrs=attrs,
                                                           extra=extra)]))[0]
            self.assertTrue(e['symlink'], attrs or extra)
            self.assertEqual(e['kind'], 'other')
        plain = km.parse_listing(sevenz_text([sevenz_block('Root/K/f.wav', attrs='A -rw-r--r--')]))
        self.assertFalse(plain[0]['symlink'])
        e = km.parse_listing(unrar_text([unrar_block('Root/K/l.wav').replace(
            '..A....', 'lrwxrwxrwx')]))[0]
        self.assertTrue(e['symlink'])

    def test_pack_crc_and_blake2_never_used_as_file_crc(self):
        block = unrar_block('Root/K/a.wav').replace('       CRC32: AAAA0001\n', '  Pack-CRC32: 12345678\n  BLAKE2: ' + 'ab' * 32 + '\n')
        self.assertEqual(km.parse_listing(unrar_text([block]))[0]['crc32'], '')

    def test_malformed_crc_becomes_empty(self):
        for bad in ('', 'XYZ', '1234567', '123456789', 'GGGGGGGG'):
            e = km.parse_listing(unrar_text([unrar_block('Root/K/a.wav', crc=bad)]))[0]
            self.assertEqual(e['crc32'], '', bad)

    def test_listing_must_start_with_known_banner(self):
        with self.assertRaises(km.ManifestError):
            km.parse_listing('hello\n        Name: x\n')


class RuleHardeningTests(unittest.TestCase):
    def test_unsafe_names_excluded(self):
        bad = ['R/K/ctl\x1b[31m.wav', 'R/K/bidi\u202e.wav', 'R/K/tab\t.wav', 'R/K//a.wav',
               'R/K/./a.wav', 'R/K/a.wav ', ' R/K/a.wav', '@R/K/a.wav', 'R/K/zero\u200b.wav',
               'R/K/a\\b.wav', 'R/../K/a.wav', 'R/K/a?.wav']
        entries = [entry(b, crc='%08X' % i) for i, b in enumerate(bad)] + [entry('R/K/ok.wav')]
        rows, recon = km.build_rows(entries, ARCHIVE)
        self.assertEqual([r['reason'] for r in rows], ['unsafe_name'] * len(bad) + [''])
        self.assertEqual(recon['excluded_by_reason'], {'unsafe_name': len(bad)})
        for b in bad:
            with self.assertRaises(km.ManifestError, msg=repr(b)):
                km.check_member_path(b)

    def test_plan_extraction_rejects_unsafe_included_row(self):
        rows, _ = km.build_rows([entry('R/K/ok.wav')], ARCHIVE)
        rows[0]['archive_path'] = 'R/K/\u202eok.wav'
        with self.assertRaises(km.ManifestError):
            km.plan_extraction(rows)

    def test_plan_uses_path_suffix_not_ext_column(self):
        rows, _ = km.build_rows([entry('R/K/ok.wav')], ARCHIVE)
        rows[0]['archive_path'] = 'R/K/ok.mp3'  # ext column still says wav
        with self.assertRaises(km.ManifestError):
            km.plan_extraction(rows)

    def test_empty_or_bad_crc_not_deduplicated(self):
        for crc in ('', 'ZZZZZZZZ'):
            entries = [entry('R/K/a.wav', 50, km._clean_crc(crc)), entry('R/K/b.wav', 50, km._clean_crc(crc))]
            rows, _ = km.build_rows(entries, ARCHIVE)
            self.assertTrue(all(r['include'] for r in rows), crc)
        rows, _ = km.build_rows([entry('R/K/a.wav', 50, 'AABBCCDD'), entry('R/K/b.wav', 50, 'AABBCCDD')], ARCHIVE)
        self.assertEqual([r['include'] for r in rows], [True, False])

    def test_too_large_excluded(self):
        rows, _ = km.build_rows([entry('R/K/a.wav', 2000), entry('R/K/b.wav', 1000, 'CD000000')], ARCHIVE,
                                max_file_bytes=1000)
        self.assertEqual([r['reason'] for r in rows], ['too_large', ''])

    def test_acquisition_case_insensitive_and_unmatched_reported(self):
        rows, recon = km.build_rows([entry('R/Kit A/a.wav')], ARCHIVE,
                                    {'KIT a': 'purchased', 'nothing here': 'free'})
        self.assertEqual(rows[0]['acquisition_status'], 'purchased')
        self.assertEqual(recon['unmatched_acquisition'], ['nothing here'])

    def test_unmatched_recycled_pattern_reported(self):
        _, recon = km.build_rows([entry('R/Kit A/a.wav')], ARCHIVE, None,
                                 (('kit', 'a'), ('nomatch',)))
        self.assertEqual(recon['unmatched_recycled'], [('nomatch',)])

    def test_reconciliation_hides_names_unless_verbose(self):
        _, recon = km.build_rows([entry('R/Kit A/odd\x1b[2J.xyz')], ARCHIVE)
        quiet = km.format_reconciliation(recon)
        self.assertNotIn('Kit A', quiet)
        self.assertNotIn('odd', quiet)
        loud = km.format_reconciliation(recon, verbose=True)
        self.assertIn('Kit A', loud)
        for text in (quiet, loud):
            self.assertNotIn('\x1b', text)
        self.assertIn('\\u001b', loud)

    def test_recycled_choice_is_stated_loudly(self):
        _, recon = km.build_rows([entry('R/K/a.wav')], ARCHIVE)
        self.assertIn('WARNING', km.format_reconciliation(recon))
        _, recon = km.build_rows([entry('R/K/a.wav')], ARCHIVE, recycled_source='x.txt')
        self.assertIn('RECYCLED-KIT LIST', km.format_reconciliation(recon))

    def test_row_validation(self):
        rows, _ = km.build_rows([entry('R/K/a.wav'), entry('R/K/b.txt', 5, 'CD000000')], ARCHIVE)
        km.validate_rows(rows)
        def broken(**change):
            bad = copy.deepcopy(rows)
            bad[0].update(change)
            with self.assertRaises(km.ManifestError, msg=str(change)):
                km.validate_rows(bad)
        broken(file_id='0' * 64)
        broken(ext='mp3')
        broken(reason='why')           # included row with a reason
        broken(verified_label='guitar')
        broken(kit_split='holdout')
        broken(duplicate_of='nope')
        broken(listing_crc32='12')

    def test_read_manifest_rejects_tampered_path(self):
        rows, _ = km.build_rows([entry('R/K/a.wav')], ARCHIVE)
        with tempfile.TemporaryDirectory() as tmp:
            path = km.write_manifest(rows, Path(tmp) / 'm.csv', base=tmp)
            path.write_text(path.read_text().replace('R/K/a.wav', 'R/K/c.wav'))
            with self.assertRaises(km.ManifestError):
                km.read_manifest(path)


class CliTests(unittest.TestCase):
    def run_cli(self, tmp, *extra, listing=None, out='m.csv'):
        lst = Path(tmp) / 'l.txt'
        if isinstance(listing, bytes):
            lst.write_bytes(listing)
        else:
            lst.write_text(listing or render('unrar'), encoding='utf-8')
        argv = ['manifest', '--listing', str(lst), *extra, str(Path(tmp) / 'private' / out)]
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            with mock.patch.object(km, 'PRIVATE_ROOT', Path(tmp) / 'private'):
                try:
                    code = km.main(argv)
                except SystemExit as exc:
                    code = exc.code
        return code, buf.getvalue(), err.getvalue()

    def test_recycled_choice_and_archive_name_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_cli(tmp, '--archive-name', ARCHIVE)[0], 2)
            self.assertEqual(self.run_cli(tmp, '--no-recycled-kits')[0], 2)
            self.assertEqual(self.run_cli(tmp, '--archive-name', ARCHIVE, '--no-recycled-kits',
                                          '--recycled-kits', 'x')[0], 2)

    def test_no_recycled_kits_prints_warning_and_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = self.run_cli(tmp, '--archive-name', ARCHIVE, '--no-recycled-kits')
            self.assertEqual(code, 0)
            self.assertIn('WARNING', out)
            self.assertNotIn('Kit A', out)
            self.assertTrue((Path(tmp) / 'private' / 'm.csv').exists())

    def test_recycled_file_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'private').mkdir()
            rec = Path(tmp) / 'private' / 'rec.txt'
            rec.write_text('# comment\nkit b\nnomatch\n')
            code, out, _ = self.run_cli(tmp, '--archive-name', ARCHIVE, '--recycled-kits', str(rec))
            self.assertEqual(code, 0)
            self.assertIn('RECYCLED-KIT LIST: 2', out)
            self.assertIn('recycled_or_collated_kit: 1', out)
            self.assertIn('matching no kit: 1', out)

    def test_listing_must_be_strict_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = self.run_cli(tmp, '--archive-name', ARCHIVE, '--no-recycled-kits',
                                        listing=b'UNRAR 7.00\n\xff\xfe')
            self.assertEqual(code, 2)
            self.assertIn('UTF-8', err)


# ---------- wav limits ----------

PCM_TAIL = b'\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71'


def raw_wav(tag=1, ch=1, rate=8000, bits=16, data=b'', declared=None, sub=1, tail=PCM_TAIL, pad=b'',
            valid=None, cb=22, riff=None, byte_rate=None, align=None, fmt_extra=b''):
    n = len(data) if declared is None else declared
    byte_rate = rate * ch * bits // 8 if byte_rate is None else byte_rate
    align = ch * bits // 8 if align is None else align
    if tag == 0xFFFE:  # WAVE_FORMAT_EXTENSIBLE: 40-byte fmt chunk (+ fmt_extra)
        fmt = struct.pack('<HHIIHH', tag, ch, rate, byte_rate, align, bits)
        fmt += struct.pack('<HHI', cb, bits if valid is None else valid, 4) + struct.pack('<H', sub) + tail
        fmt += fmt_extra
        head = 4 + 8 + len(fmt) + 8
    else:
        fmt = struct.pack('<HHIIHH', tag, ch, rate, byte_rate, align, bits) + fmt_extra
        head = 4 + 8 + len(fmt) + 8
    size = min(head + n, 2**32 - 1) if riff is None else riff
    return (b'RIFF' + struct.pack('<I', size) + b'WAVEfmt ' + struct.pack('<I', len(fmt)) + fmt
            + b'data' + struct.pack('<I', n) + data + pad)


@unittest.skipIf(np is None, 'numpy not installed')
class WavLimitTests(unittest.TestCase):
    def reason(self, blob):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(blob)
            row = {f: '' for f in km.FIELDS}
            row.update(file_id='1', archive_path='a.wav', include=True)
            km.measure_rows(tmp, [row])
            return row['reason']

    def test_ok_wav_passes(self):
        self.assertEqual(self.reason(raw_wav(data=b'\x01\x00' * 100)), '')

    def test_unsupported_formats_still_rejected(self):
        self.assertEqual(self.reason(raw_wav(tag=3, bits=16, data=b'\0' * 400)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=2, bits=4, data=b'\0' * 400)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=0xFFFE, sub=2, data=b'\0' * 400)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=0xFFFE, tail=b'\x01' * 14, data=b'\0' * 400)),
                         'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(rate=0, data=b'\0' * 4)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(rate=km.MAX_RATE + 1, data=b'\0' * 4)), 'unsupported_wav_format')

    def test_declared_size_checked_before_reading(self):
        self.assertEqual(self.reason(raw_wav(data=b'\0' * 100, declared=0xFFFFFFF0)), 'wav_too_large')

    def test_channel_limit(self):
        self.assertEqual(self.reason(raw_wav(ch=64, data=b'\0' * 128)), 'unsupported_wav_format')

    def test_truncated_and_garbage(self):
        self.assertEqual(self.reason(raw_wav(data=b'\0' * 100, declared=1000)), 'unreadable_wav')
        self.assertEqual(self.reason(b'not a wav'), 'unreadable_wav')

    def test_memory_error_excludes_row_and_run_continues(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_wav(Path(tmp) / 'ok.wav', [1, 2, 3] * 20)
            rows = []
            for i, n in enumerate(('boom.wav', 'ok.wav')):
                r = {f: '' for f in km.FIELDS}
                r.update(file_id=str(i), archive_path=n, include=True)
                rows.append(r)
            real = km.decode_wav_prefix
            def fake(path, *a, **kw):
                if Path(path).name == 'boom.wav':
                    raise MemoryError
                return real(path, *a, **kw)
            with mock.patch.object(km, 'decode_wav_prefix', fake):
                km.measure_rows(tmp, rows)
            self.assertEqual(rows[0]['reason'], 'unreadable_wav')
            self.assertTrue(rows[1]['include'])

    def test_analysis_window_is_capped_but_duration_is_full(self):
        rate = 8000
        x = np.zeros(rate * 60)
        x[rate * 40:rate * 40 + 400] = np.hanning(400)
        dur, onsets, _ = km.signal_measures(x, rate)
        self.assertEqual(dur, 60.0)
        self.assertEqual(onsets, 0)
        self.assertEqual(km.signal_measures(x, rate, max_seconds=50)[1], 1)


# ---------- extraction with a fake unrar ----------

FAKE_UNRAR = textwrap.dedent('''\
    #!/usr/bin/env python3
    """Stand-in for unrar: writes fixture files named in the @list argument."""
    import os, struct, sys, wave
    args = sys.argv[1:]
    if not args:
        print('\\nUNRAR %s freeware      Copyright' % os.environ.get('FAKE_VERSION', '7.00'))
        sys.exit(0)
    assert args[0] == 'x' and '--' in args and '-scfl' in args, args
    assert os.environ.get('LC_ALL') == 'C.UTF-8'
    dest = args[-1]
    raw = open(args[-2][1:], encoding='utf-8').read()
    if os.environ.get('FAKE_LOG'):
        open(os.environ['FAKE_LOG'], 'w').write(raw + '\\nARGS ' + ' '.join(args[:args.index('--')]))
    if os.environ.get('FAKE_FAIL'):
        sys.stderr.write('boom \\x1b[31mred')
        sys.exit(int(os.environ['FAKE_FAIL']))
    for n in filter(None, raw.split('\\n')):
        path = os.path.join(dest, n)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if n.endswith('bad.wav'):
            open(path, 'wb').write(b'not a wav')
        elif n.endswith('link.wav'):
            os.symlink('/etc/hostname', path)
        elif n.endswith('float.wav'):
            hdr = (b'RIFF' + struct.pack('<I', 40) + b'WAVEfmt ' + struct.pack('<IHHIIHH', 16, 3, 1, 8000, 32000, 4, 32)
                   + b'data' + struct.pack('<I', 4) + b'\\0\\0\\xc0\\x7f')
            open(path, 'wb').write(hdr)
        else:
            seed = 5 if n.endswith('dup2.wav') else len(n)
            with wave.open(path, 'wb') as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                w.writeframes(b''.join(struct.pack('<h', (i * seed) % 2000 - 1000) for i in range(800)))
            if n.endswith('hard.wav'):
                os.link(path, os.path.join(os.path.dirname(dest.rstrip('/')), 'extra-hardlink'))
''')

WAV_SIZE = 1644  # 44-byte header + 800 16-bit samples
EXTRACT_NAMES = ['R/K/dup1.wav', 'R/K/dup2.wav', 'R/K/bad.wav', 'R/K/link.wav', 'R/K/hard.wav',
                 'R/K/float.wav']


@unittest.skipIf(np is None, 'numpy not installed')
class ExtractFlowTests(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        self.tmp = Path(tmpdir.name)
        self.private, self.kits = self.tmp / 'private', self.tmp / 'private' / 'kits'
        self.kits.mkdir(parents=True)
        self.fake = self.tmp / 'fake-unrar'
        self.fake.write_text(FAKE_UNRAR)
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        sizes = {'bad.wav': 9, 'float.wav': 48, 'link.wav': 11}
        entries = [entry(n, sizes.get(n.rsplit('/', 1)[1], WAV_SIZE), '') for n in EXTRACT_NAMES]
        entries.insert(2, entry('R/K/notes.txt', 5, 'CD000000'))
        rows, _ = km.build_rows(entries, ARCHIVE)
        for r in rows:
            r['listing_crc32'] = ''
        self.manifest = km.write_manifest(rows, self.private / 'm.csv', base=self.private)
        self.log = self.tmp / 'fake.log'
        patcher = mock.patch.dict(os.environ, {'FAKE_LOG': str(self.log)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_extract(self, out='run1', **kw):
        kw.setdefault('private_base', self.private)
        kw.setdefault('kits_base', self.kits)
        return km.extract(ARCHIVE, self.manifest, self.kits / out, str(self.fake), **kw)

    def test_extract_results_and_default_output_manifest(self):
        before = self.manifest.read_bytes()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out_rows = self.run_extract()
        self.assertEqual(self.manifest.read_bytes(), before)  # source manifest untouched
        dest = self.private / 'm.csv.extracted.csv'
        self.assertTrue(dest.exists())
        got = by_path(out_rows)
        self.assertEqual(got['R/K/bad.wav']['reason'], 'unreadable_wav')
        self.assertEqual(got['R/K/link.wav']['reason'], 'extract_not_regular_file')
        self.assertEqual(got['R/K/hard.wav']['reason'], 'extract_hardlinked')
        self.assertEqual(got['R/K/float.wav']['reason'], 'nonfinite_samples')
        self.assertTrue(got['R/K/dup1.wav']['include'])
        self.assertTrue(got['R/K/dup2.wav']['include'])  # different samples, not a PCM duplicate
        self.assertEqual(len(got['R/K/dup1.wav']['pcm_sha256']), 64)
        self.assertEqual(got['R/K/dup1.wav']['duration_s'], '0.1000')
        self.assertTrue(all(r['verified_label'] == '' for r in out_rows))
        self.assertEqual(as_text(km.read_manifest(dest)), as_text(out_rows))
        text = buf.getvalue()
        self.assertIn('nonfinite_samples: 1', text)
        self.assertIn('unreadable_wav: 1', text)

    def test_in_place_overwrites_source(self):
        self.run_extract(in_place=True)
        self.assertFalse((self.private / 'm.csv.extracted.csv').exists())
        self.assertTrue(any(r['pcm_sha256'] for r in km.read_manifest(self.manifest)))
        with self.assertRaises(km.ManifestError):
            self.run_extract('run2', in_place=True, out_manifest=self.private / 'o.csv')

    def test_listfile_has_exactly_the_planned_names_and_utf8_switch(self):
        planned = [r['archive_path'] for r in km.read_manifest(self.manifest) if r['include']]
        self.assertNotIn('R/K/notes.txt', planned)
        self.run_extract()
        text = self.log.read_text()
        listed, args = text.split('\nARGS ')
        self.assertEqual(listed, '\n'.join(planned) + '\n')
        for flag in ('x', '-p-', '-o-', '-scfl'):
            self.assertIn(flag, args.split())

    def test_existing_output_dir_refused(self):
        (self.kits / 'run1').mkdir()
        with self.assertRaises(km.ManifestError):
            self.run_extract()
        self.assertFalse(self.log.exists())

    def test_nonzero_exit_aborts_with_clean_message(self):
        with mock.patch.dict(os.environ, {'FAKE_FAIL': '3'}):
            with self.assertRaises(km.ManifestError) as cm:
                self.run_extract()
        self.assertIn('exited 3', str(cm.exception))
        self.assertNotIn('\x1b', str(cm.exception))
        self.assertFalse((self.private / 'm.csv.extracted.csv').exists())

    def test_old_unrar_refused_before_anything_is_created(self):
        with mock.patch.dict(os.environ, {'FAKE_VERSION': '6.11'}):
            with self.assertRaises(km.ManifestError) as cm:
                self.run_extract()
        self.assertIn('too old', str(cm.exception))
        self.assertFalse((self.kits / 'run1').exists())
        with mock.patch.dict(os.environ, {'FAKE_VERSION': '6.12'}):
            self.assertEqual(km.check_unrar_version(str(self.fake)), (6, 12))

    def test_total_size_cap_and_disk_check(self):
        with self.assertRaises(km.ManifestError):
            self.run_extract(max_total=10)
        self.assertFalse((self.kits / 'run1').exists())
        fake_usage = mock.Mock(free=1000)
        with mock.patch.object(km.shutil, 'disk_usage', return_value=fake_usage):
            with self.assertRaises(km.ManifestError):
                self.run_extract()

    def test_archive_name_must_match_manifest(self):
        with self.assertRaises(km.ManifestError):
            km.extract('OTHER.rar', self.manifest, self.kits / 'run1', str(self.fake),
                       private_base=self.private, kits_base=self.kits)

    def test_output_capture_is_capped(self):
        code = 'import sys; sys.stdout.write("x" * 100000)'
        rc, out, _ = km.run_capped([sys.executable, '-c', code], 30)
        self.assertEqual((rc, len(out)), (0, km.CAPTURE_CAP))

    def test_pcm_duplicates_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            write_wav(out / 'a.wav', [1, 2, 3, 4] * 10)
            write_wav(out / 'b.wav', [1, 2, 3, 4] * 10)
            rows = []
            for i, n in enumerate(('a.wav', 'b.wav')):
                r = {f: '' for f in km.FIELDS}
                r.update(file_id=str(i), archive_path=n, include=True)
                rows.append(r)
            km.measure_rows(out, rows)
            self.assertTrue(rows[0]['include'])
            self.assertFalse(rows[1]['include'])
            self.assertEqual(rows[1]['reason'], 'duplicate_pcm_sha256')
            self.assertEqual(rows[1]['duplicate_of'], '0')

    def test_verify_rejects_hardlink(self):
        out = self.tmp / 'o'
        (out / 'R').mkdir(parents=True)
        (out / 'R' / 'a.wav').write_bytes(b'abc')
        os.link(out / 'R' / 'a.wav', self.tmp / 'second-name')
        row = {'archive_path': 'R/a.wav', 'listing_size': '3', 'listing_crc32': ''}
        self.assertEqual(km.verify_extracted(out, row), 'extract_hardlinked')


if __name__ == '__main__':
    unittest.main()


def tone_f32(n=800, ch=1):
    t = np.arange(n) / 8000
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype('<f4')
    return np.repeat(x, ch).astype('<f4') if ch > 1 else x


@unittest.skipIf(np is None, 'numpy not installed')
class FloatWavTests(unittest.TestCase):
    def decode(self, blob):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.wav'
            p.write_bytes(blob)
            return km.decode_wav(p)

    def reason(self, blob):
        with self.assertRaises(km.WavProblem) as cm:
            self.decode(blob)
        return cm.exception.reason

    def test_float32_and_float64_decode_and_measure(self):
        x32 = tone_f32()
        a, rate, h32 = self.decode(raw_wav(tag=3, bits=32, data=x32.tobytes()))
        self.assertEqual(rate, 8000)
        np.testing.assert_allclose(a, x32.astype(np.float64))
        _, _, centroid = km.signal_measures(a, rate)
        self.assertAlmostEqual(centroid, 1000, delta=40)
        b, _, h64 = self.decode(raw_wav(tag=3, bits=64, data=x32.astype('<f8').tobytes()))
        np.testing.assert_allclose(b, a)
        self.assertNotEqual(h32, h64)  # width is part of the hash

    def test_extensible_float_and_pcm(self):
        x32 = tone_f32()
        a, _, ha = self.decode(raw_wav(tag=0xFFFE, bits=32, sub=3, data=x32.tobytes()))
        plain, _, hp = self.decode(raw_wav(tag=3, bits=32, data=x32.tobytes()))
        np.testing.assert_allclose(a, plain)
        self.assertEqual(ha, hp)  # same payload, same hash whichever header carries it
        pcm = np.array([0, 1000, -1000, 500], dtype='<i2').tobytes()
        e, _, he = self.decode(raw_wav(tag=0xFFFE, bits=16, sub=1, data=pcm))
        p, _, hpcm = self.decode(raw_wav(tag=1, bits=16, data=pcm))
        np.testing.assert_allclose(e, p)
        self.assertEqual(he, hpcm)

    def test_float_and_int_hashes_never_collide(self):
        raw = bytes(range(16))
        _, _, hf = self.decode(raw_wav(tag=3, bits=32, data=raw))
        _, _, hi = self.decode(raw_wav(tag=1, bits=32, data=raw))
        self.assertNotEqual(hf, hi)

    def test_pcm_hash_matches_original_scheme(self):
        raw = np.array([0, 1000, -1000, 500], dtype='<i2').tobytes()
        _, _, h = self.decode(raw_wav(data=raw))
        self.assertEqual(h, hashlib.sha256(b'1:2:8000:' + raw).hexdigest())
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'w.wav'
            write_wav(p, [0, 1000, -1000, 500], rate=8000)
            self.assertEqual(km.decode_wav(p)[2], h)

    def test_24bit_pcm_and_stereo_float(self):
        data = (0x400000).to_bytes(3, 'little') + (-0x400000 & 0xFFFFFF).to_bytes(3, 'little')
        x, _, _ = self.decode(raw_wav(bits=24, data=data))
        np.testing.assert_allclose(x, [0.5, -0.5])
        st = np.array([0.5, -0.5, 0.25, 0.25], dtype='<f4')
        x, _, _ = self.decode(raw_wav(tag=3, ch=2, bits=32, data=st.tobytes()))
        np.testing.assert_allclose(x, [0.0, 0.25])

    def test_nonfinite_samples_rejected(self):
        for bad in (np.nan, np.inf, -np.inf):
            arr = tone_f32()
            arr[10] = bad
            self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=arr.tobytes())), 'nonfinite_samples')
            self.assertEqual(self.reason(raw_wav(tag=3, bits=64, data=arr.astype('<f8').tobytes())),
                             'nonfinite_samples')
        big = np.array([1e308, 1e308], dtype='<f8')  # finite; scaled by 1/ch before summing
        x, _, _ = self.decode(raw_wav(tag=3, ch=2, bits=64, data=big.tobytes()))
        self.assertTrue(np.isfinite(x).all() and x[0] == 1e308)

    def test_silence_is_measured_not_excluded(self):
        x, rate, _ = self.decode(raw_wav(tag=3, bits=32, data=b'\0' * 400))
        self.assertEqual(km.signal_measures(x, rate), (len(x) / rate, 0, None))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(raw_wav(tag=3, bits=32, data=b'\0' * 400))
            row = {f: '' for f in km.FIELDS}
            row.update(file_id='1', archive_path='a.wav', include=True)
            km.measure_rows(tmp, [row])
            self.assertTrue(row['include'])
            self.assertEqual((row['reason'], row['centroid_hz'], row['onset_count']), ('', '', 0))

    def test_huge_finite_values_do_not_crash_measures(self):
        arr = np.full(2000, 1e200, dtype='<f8')
        x, rate, _ = self.decode(raw_wav(tag=3, bits=64, data=arr.tobytes()))
        dur, _, centroid = km.signal_measures(x, rate)
        self.assertTrue(centroid is None or np.isfinite(centroid))

    def test_truncated_and_oversized_chunks(self):
        d = tone_f32().tobytes()
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, declared=len(d) + 400)), 'unreadable_wav')
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, declared=0xFFFFFFFF)), 'wav_too_large')
        # fmt chunk declaring more bytes than the file holds
        blob = b'RIFF' + struct.pack('<I', 28) + b'WAVEfmt ' + struct.pack('<I', 900) + b'\0' * 16
        self.assertEqual(self.reason(blob), 'unreadable_wav')
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d)[:30]), 'unreadable_wav')
        self.assertEqual(self.reason(b'RIFF'), 'unreadable_wav')

    def test_hostile_header_with_huge_frame_count_reads_nothing(self):
        d = tone_f32().tobytes()
        real_open = open
        for declared in (km.MAX_DECODE_BYTES + 64, 2 ** 31, 0xFFFFFFF0, km.MAX_DECODE_SAMPLES * 8 + 8):
            for blob in (raw_wav(tag=3, ch=8, bits=64, data=d, declared=declared),
                         raw_wav(tag=1, ch=8, bits=8, data=d, declared=declared)):
                with tempfile.TemporaryDirectory() as tmp:
                    p = Path(tmp) / 'a.wav'
                    p.write_bytes(blob)
                    reads = []

                    class Spy:
                        def __init__(self, f):
                            self.f = f

                        def read(self, n=-1):
                            reads.append(n)
                            return self.f.read(n)

                        def __getattr__(self, name):
                            return getattr(self.f, name)

                        def __enter__(self):
                            return self

                        def __exit__(self, *a):
                            self.f.close()

                    with mock.patch.object(km, 'open', lambda *a, **k: Spy(real_open(*a, **k)), create=True):
                        with self.assertRaises(km.WavProblem) as cm:
                            km.decode_wav(p)
                    self.assertEqual(cm.exception.reason, 'wav_too_large')
                    self.assertLessEqual(sum(reads), 12 + 8 + 16 + 8)  # headers only, no sample data

    def test_data_just_under_cap_but_beyond_file_is_unreadable(self):
        d = tone_f32().tobytes()
        edge = km.MAX_DECODE_SAMPLES * 4  # float32 at exactly the sample cap
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, declared=edge)), 'unreadable_wav')
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, declared=edge + 4)), 'wav_too_large')
        # a partial frame counts toward the cap: one byte past the cap is already too large
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, declared=edge + 1)), 'wav_too_large')
        self.assertEqual(self.reason(raw_wav(tag=1, bits=8, data=d, declared=km.MAX_DECODE_SAMPLES)),
                         'unreadable_wav')
        self.assertEqual(self.reason(raw_wav(tag=1, bits=8, data=d, declared=km.MAX_DECODE_SAMPLES + 1)),
                         'wav_too_large')  # width-1 memory-cap case

    def test_trailing_partial_frame_is_dropped(self):
        x, _, _ = self.decode(raw_wav(tag=3, bits=32, data=tone_f32(10).tobytes() + b'\x01\x02'))
        self.assertEqual(len(x), 10)

    def test_extra_chunks_odd_padding_and_order(self):
        d = tone_f32(20).tobytes()
        fmt = struct.pack('<HHIIHH', 3, 1, 8000, 32000, 4, 32)

        def build(chunks):
            body = b'WAVE' + b''.join(
                cid + struct.pack('<I', len(c)) + c + (b'\0' if len(c) & 1 else b'') for cid, c in chunks)
            return b'RIFF' + struct.pack('<I', len(body)) + body

        ok = build([(b'LIST', b'abc'), (b'fmt ', fmt), (b'junk', b'12345'), (b'data', d)])
        self.assertEqual(len(self.decode(ok)[0]), 20)
        # odd-sized chunk whose pad byte is missing shifts the next header, so it must not parse
        no_pad = b'RIFF' + struct.pack('<I', 0) + b'WAVE' + b'LIST' + struct.pack('<I', 3) + b'abc' \
            + b'fmt ' + struct.pack('<I', 16) + fmt + b'data' + struct.pack('<I', len(d)) + d
        self.assertEqual(self.reason(no_pad), 'unreadable_wav')
        self.assertEqual(self.reason(build([(b'data', d), (b'fmt ', fmt)])), 'unreadable_wav')
        self.assertEqual(self.reason(build([(b'fmt ', fmt), (b'fmt ', fmt), (b'data', d)])), 'unreadable_wav')
        self.assertEqual(self.reason(build([(b'fmt ', fmt)])), 'unreadable_wav')
        # an odd-sized data chunk at the end with no pad byte is fine
        odd = raw_wav(tag=1, bits=8, data=b'\x80' * 7)
        self.assertEqual(len(self.decode(odd)[0]), 7)
        self.assertEqual(len(self.decode(raw_wav(tag=1, bits=8, data=b'\x80' * 7, pad=b'\0'))[0]), 7)

    def test_nonfinite_trailing_scalar_of_a_partial_frame_is_rejected(self):
        stereo = np.array([0.5, -0.5, 0.25, 0.25], dtype='<f4').tobytes()
        for bad in (np.nan, np.inf):
            tail = np.array([bad], dtype='<f4').tobytes()  # one complete scalar, half a stereo frame
            self.assertEqual(self.reason(raw_wav(tag=3, ch=2, bits=32, data=stereo + tail)), 'nonfinite_samples')
        ok = np.array([0.1], dtype='<f4').tobytes()
        self.assertEqual(len(self.decode(raw_wav(tag=3, ch=2, bits=32, data=stereo + ok))[0]), 2)

    def test_empty_data_wavs_are_unreadable_and_never_deduplicated(self):
        self.assertEqual(self.reason(raw_wav(tag=1, bits=16, data=b'')), 'empty_wav_data')
        self.assertEqual(self.reason(raw_wav(tag=1, bits=16, data=b'\x01')), 'empty_wav_data')  # less than one frame
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for i in range(2):
                (Path(tmp) / f'{i}.wav').write_bytes(raw_wav(tag=1, bits=16, data=b''))
                row = {f: '' for f in km.FIELDS}
                row.update(file_id=str(i), archive_path=f'{i}.wav', include=True)
                rows.append(row)
            km.measure_rows(tmp, rows)
            self.assertEqual([(r['include'], r['reason'], r['duplicate_of']) for r in rows],
                             [(False, 'empty_wav_data', '')] * 2)

    def test_files_with_many_valid_chunks_are_accepted(self):
        d = tone_f32(20).tobytes()
        fmt = struct.pack('<HHIIHH', 3, 1, 8000, 32000, 4, 32)
        chunks = b''.join(b'cue ' + struct.pack('<I', 0) for _ in range(1000))
        body = b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt + chunks + b'data' + struct.pack('<I', len(d)) + d
        self.assertEqual(len(self.decode(b'RIFF' + struct.pack('<I', len(body)) + body)[0]), 20)

    def test_analysis_prefix_is_capped_in_samples(self):
        data = tone_f32(500).tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'a.wav'
            path.write_bytes(raw_wav(tag=3, bits=32, rate=1000, data=data))
            with mock.patch.object(km, 'MAX_ANALYSIS_SAMPLES', 100):
                x, _, digest, total = km.decode_wav_prefix(path)
        self.assertEqual((len(x), total), (100, 500))
        self.assertEqual(digest, hashlib.sha256(b'f:1:4:1000:' + data).hexdigest())  # the hash still covers all data

    def test_chunk_walk_is_bounded(self):
        fmt = struct.pack('<HHIIHH', 3, 1, 8000, 32000, 4, 32)
        chunks = b''.join(b'junk' + struct.pack('<I', 0) for _ in range(km._MAX_CHUNKS + 5))
        body = b'WAVE' + b'fmt ' + struct.pack('<I', 16) + fmt + chunks
        self.assertEqual(self.reason(b'RIFF' + struct.pack('<I', len(body)) + body), 'unreadable_wav')

    def test_channel_limits(self):
        d = b'\0' * 64
        self.assertEqual(self.reason(raw_wav(tag=3, ch=0, bits=32, data=d)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=3, ch=9, bits=32, data=d)), 'unsupported_wav_format')
        self.assertEqual(len(self.decode(raw_wav(tag=3, ch=8, bits=32, data=d))[0]), 2)


    def test_integer_value_mapping(self):
        x, _, _ = self.decode(raw_wav(bits=8, data=bytes([0, 128, 255])))
        np.testing.assert_allclose(x, [-1.0, 0.0, 127 / 128])
        x, _, _ = self.decode(raw_wav(bits=32, data=np.array([-2 ** 31, 0, 2 ** 30, 2 ** 31 - 1], '<i4').tobytes()))
        np.testing.assert_allclose(x, [-1.0, 0.0, 0.5, (2 ** 31 - 1) / 2 ** 31])
        b24 = lambda v: (v & 0xFFFFFF).to_bytes(3, 'little')
        x, _, _ = self.decode(raw_wav(bits=24, data=b24(-1) + b24(0x7FFFFF) + b24(-0x800000) + b24(0)))
        np.testing.assert_allclose(x, [-1 / 8388608, 8388607 / 8388608, -1.0, 0.0])
        x, _, _ = self.decode(raw_wav(bits=24, ch=2, data=b24(0x400000) + b24(-0x400000) + b24(0x7FFFFF) + b24(-0x800000)))
        np.testing.assert_allclose(x, [0.0, -0.5 / 8388608])

    def test_24_in_32_is_full_scale_container(self):
        # valid bits are left-justified in the container, so no shift: this is just int32 full scale
        data = np.array([0x7FFFFF00, -0x80000000], '<i4').tobytes()
        x, _, _ = self.decode(raw_wav(tag=0xFFFE, bits=32, valid=24, sub=1, data=data))
        np.testing.assert_allclose(x, [0x7FFFFF00 / 2 ** 31, -1.0])
        for valid in (0, 33):
            self.assertEqual(self.reason(raw_wav(tag=0xFFFE, bits=32, valid=valid, data=b'\0' * 8)),
                             'unsupported_wav_format')

    def test_odd_container_bits_rejected(self):
        for bits in (12, 20, 0, 48):
            self.assertEqual(self.reason(raw_wav(bits=bits, data=b'\0' * 8)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=0xFFFE, bits=20, valid=20, data=b'\0' * 8)),
                         'unsupported_wav_format')

    def test_extensible_cbsize_rules(self):
        d = tone_f32(8).tobytes()
        ok = raw_wav(tag=0xFFFE, bits=32, sub=3, cb=24, fmt_extra=b'\0\0', data=d)
        self.assertEqual(len(self.decode(ok)[0]), 8)
        self.assertEqual(self.reason(raw_wav(tag=0xFFFE, bits=32, sub=3, cb=30, data=d)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(tag=0xFFFE, bits=32, sub=3, cb=21, data=d)), 'unsupported_wav_format')
        # plain fmt with a cbSize that does not fit inside the chunk
        self.assertEqual(self.reason(raw_wav(fmt_extra=struct.pack('<H', 5), data=b'\0' * 8)),
                         'unsupported_wav_format')
        self.assertEqual(len(self.decode(raw_wav(fmt_extra=struct.pack('<H', 0), data=b'\0' * 8))[0]), 4)

    def test_rate_and_fmt_size_boundaries(self):
        self.assertEqual(len(self.decode(raw_wav(rate=km.MAX_RATE, data=b'\0' * 8))[0]), 4)
        self.assertEqual(self.reason(raw_wav(rate=768001, data=b'\0' * 8)), 'unsupported_wav_format')
        at = struct.pack('<H', 0) + b'\0' * (km._MAX_FMT_BYTES - 18)
        self.assertEqual(len(self.decode(raw_wav(fmt_extra=at, data=b'\0' * 8))[0]), 4)
        over = at + b'\0\0'
        self.assertEqual(self.reason(raw_wav(fmt_extra=over, data=b'\0' * 8)), 'unreadable_wav')
        self.assertEqual(self.reason(raw_wav(fmt_extra=b'', data=b'\0' * 8)[:20] + b'\x0f\0\0\0'), 'unreadable_wav')

    def test_block_align_and_byte_rate_consistency(self):
        d = b'\0' * 8
        self.assertEqual(self.reason(raw_wav(align=3, data=d)), 'unsupported_wav_format')
        self.assertEqual(self.reason(raw_wav(byte_rate=1234, data=d)), 'unsupported_wav_format')
        flags = []
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.wav'
            p.write_bytes(raw_wav(align=0, byte_rate=0, data=d))
            km.decode_wav_prefix(p, flags=flags)
        self.assertEqual(flags, ['fmt_derived_fields_zero'])

    def test_riff_size_rules(self):
        d = tone_f32(10).tobytes()
        base = raw_wav(tag=3, bits=32, data=d)

        def flags_of(blob):
            with tempfile.TemporaryDirectory() as tmp:
                p = Path(tmp) / 'a.wav'
                p.write_bytes(blob)
                fl = []
                x, _, _, _ = km.decode_wav_prefix(p, flags=fl)
                self.assertEqual(len(x), 10)
                return fl

        self.assertEqual(flags_of(base), [])
        self.assertEqual(flags_of(raw_wav(tag=3, bits=32, data=d, riff=0)), ['riff_size_mismatch'])
        self.assertEqual(flags_of(raw_wav(tag=3, bits=32, data=d, riff=0xFFFFFFFF)), ['riff_size_mismatch'])
        self.assertEqual(flags_of(base + b'LIST\x00\x00\x00\x00'), ['riff_size_mismatch'])  # tail after data
        self.assertEqual(flags_of(raw_wav(tag=3, bits=32, data=d, riff=10 ** 6)), ['riff_size_mismatch'])
        # plausible RIFF end inside the data chunk with bytes after it: garbage tail, excluded
        self.assertEqual(self.reason(raw_wav(tag=3, bits=32, data=d, riff=36 + 8)), 'unreadable_wav')
        # a non-data chunk running past the declared RIFF end
        fmt = struct.pack('<HHIIHH', 3, 1, 8000, 32000, 4, 32)
        body = b'WAVEfmt ' + struct.pack('<I', 16) + fmt + b'LIST' + struct.pack('<I', 100) + b'\0' * 100
        blob = b'RIFF' + struct.pack('<I', 28) + body
        self.assertEqual(self.reason(blob), 'unreadable_wav')

    def test_measure_rows_counts_riff_flag_without_excluding(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(raw_wav(tag=3, bits=32, data=tone_f32(64).tobytes(), riff=0))
            row = {f: '' for f in km.FIELDS}
            row.update(file_id='1', archive_path='a.wav', include=True)
            checks = km.Counter()
            km.measure_rows(tmp, [row], checks)
            self.assertTrue(row['include'])
            self.assertEqual(checks, {'riff_size_mismatch': 1})

    def test_hash_covers_trailing_partial_frame(self):
        d = tone_f32(10).tobytes()
        a = self.decode(raw_wav(tag=3, bits=32, data=d + b'\x01\x02'))
        b = self.decode(raw_wav(tag=3, bits=32, data=d + b'\x01\x03'))
        c = self.decode(raw_wav(tag=3, bits=32, data=d))
        self.assertEqual(len(a[0]), 10)
        self.assertEqual(len({a[2], b[2], c[2]}), 3)
        self.assertEqual(a[2], hashlib.sha256(b'f:1:4:8000:' + d + b'\x01\x02').hexdigest())

    def test_incremental_hash_over_chunk_boundaries(self):
        n = (km._READ_CHUNK_BYTES // 4) * 2 + 37
        arr = np.sin(np.arange(n) / 50.0).astype('<f4')
        x, _, h, total = None, None, None, None
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'a.wav'
            p.write_bytes(raw_wav(tag=3, bits=32, rate=1000, data=arr.tobytes()))
            x, _, h, total = km.decode_wav_prefix(p, None)
            self.assertEqual(h, hashlib.sha256(b'f:1:4:1000:' + arr.tobytes()).hexdigest())
            np.testing.assert_allclose(x, arr.astype(np.float64))
            short, _, h2, total2 = km.decode_wav_prefix(p, 5.0)
        self.assertEqual((len(short), total2, h2), (5000, n, h))

    def test_analysis_prefix_truncation_keeps_full_duration(self):
        rate, secs = 8000, 40
        arr = (0.5 * np.sin(2 * np.pi * 500 * np.arange(rate * secs) / rate)).astype('<f4')
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(raw_wav(tag=3, bits=32, rate=rate, data=arr.tobytes()))
            x, r, h, total = km.decode_wav_prefix(Path(tmp) / 'a.wav')
            self.assertEqual((len(x), total), (int(km.MAX_ANALYSIS_S * rate), rate * secs))
            self.assertEqual(h, km.decode_wav(Path(tmp) / 'a.wav')[2])  # hash covers the whole payload
            row = {f: '' for f in km.FIELDS}
            row.update(file_id='1', archive_path='a.wav', include=True)
            km.measure_rows(tmp, [row])
            self.assertEqual(row['duration_s'], '40.0000')
            self.assertAlmostEqual(float(row['centroid_hz']), 500, delta=20)

    def test_onsets_counted_on_float_file_and_with_huge_values(self):
        rate = 16000
        for scale in (1.0, 1e200):
            arr = np.zeros(rate * 2)
            for start in (1000, 9000, 20000):
                arr[start:start + 400] = np.hanning(400) * 0.8 * scale
            x, r, _ = self.decode(raw_wav(tag=3, bits=64, rate=rate, data=arr.astype('<f8').tobytes()))
            dur, onsets, centroid = km.signal_measures(x, r)
            self.assertEqual(onsets, 3)
            self.assertTrue(centroid is not None and np.isfinite(centroid))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(raw_wav(tag=3, bits=32, rate=rate, data=(arr / 1e200).astype('<f4').tobytes()))
            row = {f: '' for f in km.FIELDS}
            row.update(file_id='1', archive_path='a.wav', include=True)
            km.measure_rows(tmp, [row])
            self.assertEqual(row['onset_count'], 3)

    def test_identical_float_payloads_are_duplicates(self):
        d = tone_f32(100).tobytes()
        with tempfile.TemporaryDirectory() as tmp:
            rows = []
            for i, name in enumerate(('a.wav', 'b.wav', 'c.wav')):
                data = d if i < 2 else tone_f32(101).tobytes()
                (Path(tmp) / name).write_bytes(raw_wav(tag=3, bits=32, data=data))
                r = {f: '' for f in km.FIELDS}
                r.update(file_id=str(i), archive_path=name, include=True)
                rows.append(r)
            km.measure_rows(tmp, rows)
        self.assertTrue(rows[0]['include'])
        self.assertEqual((rows[1]['include'], rows[1]['reason'], rows[1]['duplicate_of']),
                         (False, 'duplicate_pcm_sha256', '0'))
        self.assertTrue(rows[2]['include'])


class UnexpectedEntriesTests(unittest.TestCase):
    def test_stray_files_and_dirs_are_reported(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'k').mkdir()
            (root / 'k' / 'a.wav').write_bytes(b'x')
            (root / 'k' / 'extra.wav').write_bytes(b'x')
            (root / 'other').mkdir()
            todo = [{'archive_path': 'k/a.wav'}]
            self.assertEqual(sorted(km.unexpected_entries(root, todo)), ['k/extra.wav', 'other'])
            self.assertEqual(km.unexpected_entries(root, [{'archive_path': 'k/a.wav'},
                                                          {'archive_path': 'k/extra.wav'},
                                                          {'archive_path': 'other/z.wav'}]), [])

    def test_bad_listing_size_is_an_exclusion_not_a_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'a.wav').write_bytes(b'x')
            row = {'archive_path': 'a.wav', 'listing_size': 'abc', 'listing_crc32': ''}
            self.assertEqual(km.verify_extracted(tmp, row), 'extract_size_mismatch')
