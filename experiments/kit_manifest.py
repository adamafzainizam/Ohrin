"""Stage a private drum-kit archive: build an inclusion manifest, then extract the included WAVs.

Local-only tooling (Step K of the drum/bass masking spec). Nothing here trains or evaluates.
The archive is untrusted: paths are validated before extraction, only listed WAV rows are
extracted, nothing extracted is executed, and every output stays under data/private/.

Subcommands
  manifest  parse an `unrar lt` or `7z l -slt` listing and write manifest.csv
  extract   extract only include=true rows with unrar, then measure each WAV

Measurements (duration_s, onset_count, centroid_hz) are signal measurements made from decoded
samples. They are not listening and do not verify what a sound is. name_label comes from path
keywords and is a weak hint only. verified_label is never filled by this script.

Measurement caveats: only the first MAX_ANALYSIS_S seconds are analysed for onsets and centroid
(duration_s is the full length). Onset detection can miss hits shorter than about 60 ms and
merges onsets closer than 50 ms. The centroid is a whole-file FFT, so decay tails and DC offset
drag it. Channels are averaged to mono, which cancels phase-inverted stereo content.

Hygiene: manifest.csv holds untrusted names from the archive and cell values are NOT rewritten.
Do not open it with spreadsheet formulas enabled (a name may start with = + - @). Listings are
parsed strictly (a record that does not match the expected structure aborts, or is marked
unsafe_name). Reconciliation output shows counts only unless --verbose, and printed names are
escaped. Extraction needs unrar >= 6.12 (path-traversal fix, CVE-2022-30333); the tests never
run it against a real archive.
"""
import argparse
import csv
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zlib
from collections import Counter
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
PRIVATE_ROOT = ROOT / 'data' / 'private'
KITS_ROOT = PRIVATE_ROOT / 'kits'

FIELDS = [
    'file_id', 'archive_name', 'kit', 'archive_path', 'ext', 'listing_size', 'listing_crc32',
    'pcm_sha256', 'duplicate_of', 'acquisition_status', 'provenance_flag', 'licence_note',
    'name_label', 'duration_s', 'onset_count', 'centroid_hz', 'verified_label', 'include',
    'reason', 'kit_split',
]
ACQUISITION = ('purchased', 'free', 'shared', 'unverified')
PROVENANCE = ('none_stated', 'recycled_or_collated', 'unknown')
NAME_LABELS = ('kick', 'snare', 'hat', 'bass', 'other', 'none')
KIT_SPLITS = ('train', 'val', 'test')

MAX_FILE_BYTES = 64 * 1024 * 1024        # per-file listing_size cap (exclude too_large)
MAX_TOTAL_BYTES = 4 * 1024 ** 3          # total of included listing sizes allowed in one extract
MAX_DECODE_BYTES = 256 * 1024 * 1024     # declared data bytes allowed before any read
MAX_DECODE_SAMPLES = 16 * 1024 * 1024    # declared samples (all channels); bounds the decoded float64 set to ~128 MB
MAX_CHANNELS = 8
MAX_RATE = 768000
MAX_ANALYSIS_S = 30.0
MIN_UNRAR = (6, 12)
CAPTURE_CAP = 4096
UNRAR_TIMEOUT_S = 3600

# Extensions seen and understood as not audio for this purpose; other non-WAV types are
# still excluded but reported as unclassified so a human looks at them.
KNOWN_EXCLUDED_EXT = {
    'mp3', 'sf2', 'sfz', 'fxp', 'fxb', 'fst', 'fsc', 'mid', 'midi', 'txt', 'nfo', 'rtf', 'pdf',
    'png', 'jpg', 'jpeg', 'gif', 'url', 'html', 'htm', 'doc', 'docx', 'ini', 'db', 'ds_store',
}
ARCHIVE_EXT = {'rar', 'zip', '7z', 'tar', 'gz', 'tgz', 'bz2', 'xz', 'iso', 'cab'}

LABEL_KEYWORDS = (
    ('snare', ('snare', 'clap')),
    ('kick', ('kick',)),
    ('hat', ('hihat', 'hi-hat', 'hi hat', 'hat', 'cymbal', 'crash', 'ride')),
    ('bass', ('bass', '808')),
    ('other', ('perc', 'synth', 'vocal', 'vox', 'fx', 'guitar', 'pad', 'lead', 'tom', 'shaker',
               'rim', 'chord', 'pluck', 'bell', 'texture')),
)

CRC_RE = re.compile(r'[0-9A-Fa-f]{8}')
UNIX_LINK_RE = re.compile(r'(^|\s)l[rwxsStT-]{9}$')


class ManifestError(ValueError):
    pass


class WavProblem(ManifestError):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def safe_text(s):
    """Escape anything unprintable (controls, bidi, escapes) so names cannot affect a terminal."""
    return ''.join(c if c.isprintable() else f'\\u{ord(c):04x}' for c in str(s))


# ---------- path guards ----------

def require_under(path, base, what):
    """Resolve `path` and refuse it unless it lies inside `base`."""
    resolved = Path(path).resolve()
    base = Path(base).resolve()
    if resolved != base and base not in resolved.parents:
        raise ManifestError(f'{what} must be under {base} (got {safe_text(resolved)})')
    return resolved


def unsafe_name_problem(name):
    """Why an archive member name cannot be extracted faithfully or safely, or None."""
    if not name:
        return 'empty name'
    for c in name:
        if unicodedata.category(c).startswith('C'):
            return 'control or format character'
    if name != name.strip():
        return 'leading or trailing whitespace'
    if name.startswith('@'):
        return 'leading @'
    if '\\' in name:
        return 'backslash'
    p = PurePosixPath(name)
    if name.startswith('/') or (len(name) > 1 and name[1] == ':'):
        return 'absolute path'
    if '//' in name or name.startswith('./') or '/./' in name or name.endswith(('/.', '/')):
        return 'ambiguous path'
    if any(part == '..' for part in p.parts):
        return 'parent-directory component'
    if '*' in name or '?' in name:
        return 'wildcard character'
    return None


def check_member_path(name):
    """Reject archive member names that could escape the output dir or confuse the extractor."""
    problem = unsafe_name_problem(name)
    if problem:
        raise ManifestError(f'unsafe member name ({problem}): {safe_text(name)}')
    return name


# ---------- listing parsers ----------

def _clean_crc(value):
    value = (value or '').strip()
    return value.upper() if CRC_RE.fullmatch(value) else ''


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _entry(path, kind, size, crc32, symlink=False):
    kind = 'other' if symlink else kind
    return {'path': path, 'kind': kind, 'size': size, 'crc32': _clean_crc(crc32),
            'symlink': symlink}


def _lines(text):
    return [ln[:-1] if ln.endswith('\r') else ln for ln in text.split('\n')]


UNRAR_FIELD_RE = re.compile(r'^ +([A-Za-z0-9][A-Za-z0-9 \-]*):(?: (.*))?$')
SEVENZ_FIELD_RE = re.compile(r'^([A-Za-z][A-Za-z0-9 \-]*?) =(?: (.*))?$')


def parse_unrar_listing(text):
    """Parse `unrar lt` output. Parsing starts after the 'Details:' header line, so an archive
    comment printed earlier cannot forge entries. A name containing a newline is kept whole
    (and later marked unsafe_name); a structure that cannot be one record raises."""
    lines = _lines(text)
    start = next((i for i, ln in enumerate(lines[:200]) if ln.startswith('Details:')), None)
    if start is None:
        raise ManifestError('unrar listing has no Details: header line')
    entries, cur = [], None

    def flush():
        typ = cur['Type'].strip().lower()
        attrs = cur.get('Attributes', '')
        link = ('link' in typ or 'junction' in typ or bool(UNIX_LINK_RE.search(attrs.strip()))
                or attrs.strip().startswith('l'))
        kind = 'file' if typ == 'file' else 'dir' if typ == 'directory' else 'other'
        entries.append(_entry(cur['Name'], kind, _int(cur.get('Size')), cur.get('CRC32'), link))

    for line in lines[start + 1:]:
        if cur is not None and 'Type' not in cur:
            if line.startswith('        Type: '):
                cur['Type'] = line[14:]
            else:
                cur['Name'] += '\n' + line
            continue
        if line == '':
            if cur is not None:
                flush()
                cur = None
            continue
        if line.startswith('        Name: '):
            if cur is not None:
                flush()
            cur = {'Name': line[14:]}
            continue
        m = UNRAR_FIELD_RE.match(line)
        if not m or cur is None:
            raise ManifestError(f'unexpected line in unrar listing: {safe_text(line)[:80]}')
        key = m.group(1).strip()
        if key in cur:
            raise ManifestError(f'repeated field {key!r} inside one unrar record')
        cur[key] = (m.group(2) or '').strip()
    if cur is not None:
        if 'Type' not in cur:
            raise ManifestError('unrar listing ends inside a record')
        flush()
    return entries


def parse_7z_listing(text):
    """Parse `7z l -slt` output (records follow the first '----------' line)."""
    lines = _lines(text)
    start = next((i for i, ln in enumerate(lines[:200]) if ln == '----------'), None)
    if start is None:
        raise ManifestError('7z listing has no ---------- separator')
    entries, cur = [], None

    def flush():
        attrs = cur.get('Attributes', '')
        link = (bool(cur.get('Symbolic Link') or cur.get('Hard Link') or cur.get('Copy Link'))
                or bool(UNIX_LINK_RE.search(attrs.strip())) or attrs.strip().startswith('l'))
        is_dir = cur.get('Folder') == '+' or 'D' in attrs or attrs.strip().startswith('d')
        entries.append(_entry(cur['Path'], 'dir' if is_dir else 'file', _int(cur.get('Size')),
                              cur.get('CRC'), link))

    for line in lines[start + 1:]:
        if cur is not None and 'Folder' not in cur:
            if line.startswith('Folder = '):
                cur['Folder'] = line[9:].strip()
            else:
                cur['Path'] += '\n' + line
            continue
        if line == '':
            if cur is not None:
                flush()
                cur = None
            continue
        if line.startswith('Path = '):
            if cur is not None:
                flush()
            cur = {'Path': line[7:]}
            continue
        m = SEVENZ_FIELD_RE.match(line)
        if not m or cur is None:
            raise ManifestError(f'unexpected line in 7z listing: {safe_text(line)[:80]}')
        key = m.group(1).strip()
        if key in cur:
            raise ManifestError(f'repeated field {key!r} inside one 7z record')
        cur[key] = (m.group(2) or '').strip()
    if cur is not None:
        if 'Folder' not in cur:
            raise ManifestError('7z listing ends inside a record')
        flush()
    return entries


def parse_listing(text):
    first = next((ln for ln in _lines(text) if ln.strip()), '')
    if first.startswith(('UNRAR ', 'RAR ')):
        return parse_unrar_listing(text)
    if first.startswith('7-Zip'):
        return parse_7z_listing(text)
    raise ManifestError('listing is neither unrar lt nor 7z l -slt output')


# ---------- classification ----------

def ext_of(path):
    return PurePosixPath(path).suffix.lstrip('.').lower()


def common_root(entries):
    """The single top folder shared by every entry (e.g. the archive's own root folder), if any."""
    firsts = {PurePosixPath(e['path']).parts[0] for e in entries if e['path']}
    multi = any(len(PurePosixPath(e['path']).parts) > 1 for e in entries if e['path'])
    return firsts.pop() if len(firsts) == 1 and multi else None


def kit_of(path, root):
    parts = PurePosixPath(path).parts
    if root and parts and parts[0] == root:
        parts = parts[1:]
    return parts[0] if len(parts) > 1 else ''


def recycled_match(kit, pattern):
    k = kit.lower()
    return all(w in k for w in pattern)


def is_recycled_kit(kit, recycled=()):
    return any(recycled_match(kit, pat) for pat in recycled)


def read_recycled(path):
    """Kits whose own read-me admits recycled/collated sounds: one per line, comma-separated
    words that must all appear in the kit folder name. Kept in a private file so tracked code
    carries no kit names."""
    require_under(path, PRIVATE_ROOT, 'recycled-kits file')
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    return tuple(tuple(w.strip().lower() for w in ln.split(',') if w.strip())
                 for ln in lines if ln.strip() and not ln.startswith('#'))


def has_loop(path):
    return 'loop' in path.lower()


def name_label(path):
    """Weak keyword hint from the file name, then folders. Not a verified label."""
    p = PurePosixPath(path)
    for text in (p.name.lower(), '/'.join(p.parts[:-1]).lower()):
        for label, words in LABEL_KEYWORDS:
            if any(w in text for w in words):
                return label
    return 'none'


def file_id(archive_name, archive_path):
    return hashlib.sha256(f'{archive_name}/{archive_path}'.encode('utf-8')).hexdigest()


def build_rows(entries, archive_name, acquisition=None, recycled=(), max_file_bytes=MAX_FILE_BYTES,
               recycled_source=None):
    """Return (rows, reconciliation). Excluded rows always carry a reason."""
    acquisition = {k.lower(): v for k, v in (acquisition or {}).items()}
    root = common_root(entries)
    rows, unclassified, n_dirs = [], [], 0
    first_seen, ids, kits = {}, set(), set()
    for e in entries:
        if e['kind'] == 'dir':
            n_dirs += 1
            continue
        path = e['path']
        ext = ext_of(path)
        kit = kit_of(path, root)
        kits.add(kit.lower())
        fid = file_id(archive_name, path)
        recycled_kit = is_recycled_kit(kit, recycled)
        row = {f: '' for f in FIELDS}
        row.update(
            file_id=fid, archive_name=archive_name, kit=kit, archive_path=path, ext=ext,
            listing_size=e['size'], listing_crc32=e['crc32'],
            acquisition_status=acquisition.get(kit.lower(), 'unverified'),
            provenance_flag='recycled_or_collated' if recycled_kit else 'none_stated',
            name_label=name_label(path), include=False)
        if unsafe_name_problem(path):
            row['reason'] = 'unsafe_name'
            row['name_label'] = 'none'
            unclassified.append((path, 'unsafe_name'))
        elif e['kind'] == 'other' or e['symlink']:
            row['reason'] = 'symlink_entry' if e['symlink'] else 'non_regular_entry'
            unclassified.append((path, row['reason']))
        elif ext in ARCHIVE_EXT:
            row['reason'] = 'nested_archive'
            unclassified.append((path, row['reason']))
        elif ext != 'wav':
            row['reason'] = 'non_wav_extension'
            if ext not in KNOWN_EXCLUDED_EXT:
                unclassified.append((path, 'unknown_extension:' + (ext or '(none)')))
        elif has_loop(path):
            row['reason'] = 'loop_path'
        elif recycled_kit:
            row['reason'] = 'recycled_or_collated_kit'
        elif e['size'] <= 0:
            row['reason'] = 'empty_or_unsized'
        elif e['size'] > max_file_bytes:
            row['reason'] = 'too_large'
        else:
            key = (e['crc32'], e['size'])
            if e['crc32'] and key in first_seen:
                row['reason'] = 'duplicate_crc32_size'
                row['duplicate_of'] = first_seen[key]
            else:
                if e['crc32']:  # no listing-level dedupe without a usable CRC; pcm_sha256 later
                    first_seen[key] = fid
                row['include'] = True
        if fid in ids:
            raise ManifestError(f'duplicate file_id for {safe_text(path)} (same path listed twice?)')
        ids.add(fid)
        rows.append(row)
    validate_rows(rows)
    recon = {
        'entries': len(entries), 'directories': n_dirs, 'files': len(rows),
        'included': sum(1 for r in rows if r['include']),
        'excluded_by_reason': dict(Counter(r['reason'] for r in rows if not r['include'])),
        'unclassified': unclassified, 'root_folder_stripped': root,
        'recycled_source': recycled_source,
        'recycled_patterns': len(recycled),
        'unmatched_recycled': [pat for pat in recycled
                               if not any(recycled_match(k, pat) for k in kits)],
        'unmatched_acquisition': sorted(k for k in acquisition if k not in kits),
    }
    return rows, recon


def validate_rows(rows):
    ids = set()
    for r in rows:
        if r['include'] not in (True, False):
            raise ManifestError(f"include must be a bool for {r['file_id']}")
        if r['include'] and str(r['reason']).strip():
            raise ManifestError(f"included row carries a reason: {r['file_id']}")
        if not r['include'] and not str(r['reason']).strip():
            raise ManifestError(f"excluded row without reason: {r['file_id']}")
        if r['file_id'] != file_id(r['archive_name'], r['archive_path']):
            raise ManifestError(f"file_id does not match archive_name/archive_path: {r['file_id']}")
        if r['ext'] != ext_of(r['archive_path']):
            raise ManifestError(f"ext does not match archive_path: {r['file_id']}")
        if r['acquisition_status'] not in ACQUISITION:
            raise ManifestError(f"bad acquisition_status {r['acquisition_status']!r}")
        if r['provenance_flag'] not in PROVENANCE:
            raise ManifestError(f"bad provenance_flag {r['provenance_flag']!r}")
        if r['name_label'] not in NAME_LABELS:
            raise ManifestError(f"bad name_label {r['name_label']!r}")
        if r['verified_label'] not in ('',) + NAME_LABELS:
            raise ManifestError(f"bad verified_label {r['verified_label']!r}")
        if r['kit_split'] not in ('',) + KIT_SPLITS:
            raise ManifestError(f"bad kit_split {r['kit_split']!r}")
        if r['listing_crc32'] != '' and not CRC_RE.fullmatch(str(r['listing_crc32'])):
            raise ManifestError(f"bad listing_crc32 for {r['file_id']}")
        if _int(r['listing_size']) < -1:
            raise ManifestError(f"bad listing_size for {r['file_id']}")
        if r['file_id'] in ids:
            raise ManifestError(f"duplicate file_id {r['file_id']}")
        ids.add(r['file_id'])
    for r in rows:
        if r['duplicate_of'] and r['duplicate_of'] not in ids:
            raise ManifestError(f"duplicate_of points at an unknown file_id: {r['file_id']}")


def format_reconciliation(recon, verbose=False):
    """Counts and reasons only; member paths appear (escaped) only with verbose."""
    src = recon.get('recycled_source')
    if src:
        head = f"RECYCLED-KIT LIST: {recon['recycled_patterns']} pattern(s) from {safe_text(src)}"
    else:
        head = ('*** WARNING: --no-recycled-kits was used: recycled/collated kits are NOT '
                'excluded ***')
    lines = [head,
             f"listing entries: {recon['entries']} (directories {recon['directories']}, "
             f"files {recon['files']})",
             f"included: {recon['included']}",
             'excluded by reason:']
    for reason, n in sorted(recon['excluded_by_reason'].items()):
        lines.append(f'  {reason}: {n}')
    tags = Counter(safe_text(why) for _, why in recon['unclassified'])
    lines.append(f"unclassified entries: {len(recon['unclassified'])}")
    for why, n in sorted(tags.items()):
        lines.append(f'  {why}: {n}')
    lines.append(f"recycled-kit patterns matching no kit: {len(recon.get('unmatched_recycled', []))}")
    lines.append(f"acquisition kits matching no kit: {len(recon.get('unmatched_acquisition', []))}")
    if verbose:
        for path, why in recon['unclassified']:
            lines.append(f'  [{safe_text(why)}] {safe_text(path)}')
        for pat in recon.get('unmatched_recycled', []):
            lines.append(f'  unmatched recycled pattern: {safe_text(", ".join(pat))}')
        for k in recon.get('unmatched_acquisition', []):
            lines.append(f'  unmatched acquisition kit: {safe_text(k)}')
    return '\n'.join(lines)


# ---------- csv io ----------

def write_manifest(rows, path, base=PRIVATE_ROOT):
    out = require_under(path, base, 'manifest path')
    validate_rows(rows)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: ('true' if r[k] is True else 'false' if r[k] is False else r[k])
                        for k in FIELDS})
    return out


def read_manifest(path):
    with open(path, newline='', encoding='utf-8') as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != FIELDS:
            raise ManifestError('manifest columns do not match the schema')
        rows = []
        for r in reader:
            if r['include'] not in ('true', 'false'):
                raise ManifestError(f"bad include value {r['include']!r}")
            r['include'] = r['include'] == 'true'
            rows.append(r)
    validate_rows(rows)
    return rows


def read_acquisition(path):
    """kit,status CSV (header optional) to {lower-case kit: status}."""
    out = {}
    with open(path, newline='', encoding='utf-8') as fh:
        for rec in csv.reader(fh):
            if len(rec) < 2 or (rec[0].strip().lower() == 'kit' and rec[1].strip().lower() == 'status'):
                continue
            status, kit = rec[1].strip().lower(), rec[0].strip().lower()
            if status not in ACQUISITION:
                raise ManifestError(f'bad acquisition status {safe_text(status)!r}')
            if out.get(kit, status) != status:
                raise ManifestError('conflicting acquisition statuses for one kit')
            out[kit] = status
    return out


# ---------- extraction ----------

def utf8_env():
    return dict(os.environ, LC_ALL='C.UTF-8')


def run_capped(cmd, timeout):
    """Run a command with output captured but capped; returns (returncode, stdout, stderr)."""
    with tempfile.TemporaryDirectory() as tmp:
        o, e = Path(tmp) / 'out', Path(tmp) / 'err'
        try:
            with open(o, 'wb') as fo, open(e, 'wb') as fe:
                proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=fo, stderr=fe,
                                      env=utf8_env(), timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ManifestError(f'{Path(cmd[0]).name} timed out after {timeout}s')
        except OSError as exc:
            raise ManifestError(f'cannot run {safe_text(cmd[0])}: {exc.strerror}')
        with open(o, 'rb') as fo, open(e, 'rb') as fe:
            return (proc.returncode, fo.read(CAPTURE_CAP).decode('utf-8', 'replace'),
                    fe.read(CAPTURE_CAP).decode('utf-8', 'replace'))


def unrar_version(unrar):
    """Version tuple from the banner unrar prints when run without arguments."""
    _, out, err = run_capped([str(unrar)], 30)
    m = re.search(r'\b(?:UN)?RAR\s+(\d+)\.(\d+)', out + err)
    if not m:
        raise ManifestError('cannot read the unrar version banner')
    return int(m.group(1)), int(m.group(2))


def check_unrar_version(unrar, minimum=MIN_UNRAR):
    ver = unrar_version(unrar)
    if ver < minimum:
        raise ManifestError(f'unrar {ver[0]}.{ver[1]} is too old; need >= '
                            f'{minimum[0]}.{minimum[1]} (path-traversal fix)')
    return ver


def prepare_outdir(outdir, base=KITS_ROOT):
    """Create the output dir exclusively; it must not exist yet."""
    out = require_under(outdir, base, 'output dir')
    if out == Path(base).resolve():
        raise ManifestError('output dir must be a fresh subfolder of the kits dir')
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.mkdir(mode=0o700)
    except FileExistsError:
        raise ManifestError(f'output dir already exists (must be fresh): {safe_text(out)}')
    return out


def plan_extraction(rows):
    """Included rows whose member paths pass the guard. Any unsafe included row aborts."""
    todo = []
    for r in rows:
        if r['include']:
            check_member_path(r['archive_path'])
            if PurePosixPath(r['archive_path']).suffix.lower() != '.wav':
                raise ManifestError(f"included non-WAV row: {r['file_id']}")
            todo.append(r)
    return todo


def check_budget(todo, target, max_total=MAX_TOTAL_BYTES):
    total = sum(max(0, _int(r['listing_size'])) for r in todo)
    if total > max_total:
        raise ManifestError(f'planned extraction is {total} bytes; limit is {max_total}')
    probe = Path(target)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < total * 1.1 + (256 << 20):
        raise ManifestError(f'not enough free disk: need about {total} bytes, have {free}')
    return total


def run_unrar(unrar, archive, names, outdir, timeout=UNRAR_TIMEOUT_S):
    """Extract exactly `names` (explicit list file, no wildcards) into outdir. Executes only
    unrar. -scfl makes unrar read the list file as UTF-8; output is capped."""
    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp) / 'include.lst'
        lst.write_text('\n'.join(names) + '\n', encoding='utf-8')
        cmd = [str(unrar), 'x', '-p-', '-o-', '-idq', '-scfl', '--', str(archive),
               '@' + str(lst), str(outdir) + os.sep]
        return run_capped(cmd, timeout)


def crc32_of(path):
    crc = 0
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            crc = zlib.crc32(chunk, crc)
    return f'{crc & 0xFFFFFFFF:08X}'


def unexpected_entries(outdir, todo):
    """Entries under outdir that are not a planned file or one of its parent directories."""
    root = Path(outdir)
    planned = {Path(r['archive_path']).as_posix() for r in todo}
    parents = {q.as_posix() for p in planned for q in Path(p).parents if q.as_posix() != '.'}
    stray = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel = Path(dirpath).relative_to(root)
        for name in list(dirnames) + filenames:
            entry = (rel / name).as_posix()
            if entry not in planned and entry not in parents:
                stray.append(entry)
    return stray


def verify_extracted(outdir, row):
    """Return an exclusion reason if the extracted file is unsafe or does not match the listing."""
    target = Path(outdir) / row['archive_path']
    try:
        st = os.lstat(target)
    except FileNotFoundError:
        return 'extract_missing'
    if not os.path.isfile(target) or os.path.islink(target):
        return 'extract_not_regular_file'
    if st.st_nlink != 1:
        return 'extract_hardlinked'
    root = Path(outdir).resolve()
    if root not in target.resolve().parents:
        return 'extract_outside_outdir'
    try:
        expected = int(row['listing_size'])
    except (TypeError, ValueError):
        return 'extract_size_mismatch'
    if st.st_size != expected:
        return 'extract_size_mismatch'
    if row['listing_crc32'] and crc32_of(target) != row['listing_crc32'].upper():
        return 'extract_crc_mismatch'
    return ''


_PCM_GUID_TAIL = b'\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71'
_MAX_FMT_BYTES = 1024
_MAX_CHUNKS = 256
_READ_CHUNK_BYTES = 1 << 20


def _read_exact(f, n):
    data = f.read(n)
    if len(data) != n:
        raise WavProblem('unreadable_wav')
    return data


def parse_wav_header(f, file_size):
    """Walk the RIFF chunks of an open binary file without reading sample data.

    Returns (kind, channels, width, rate, data_offset, data_bytes, flags): kind is 'pcm' or
    'float', data_bytes is the full declared data payload (it may end in a partial frame), and
    flags lists non-fatal oddities (currently 'riff_size_mismatch', 'fmt_derived_fields_zero').
    Raises WavProblem with a reason.

    Checks, all before any sample is read:
      * every chunk lies inside the file; odd sizes are padded; fmt precedes data and appears once
      * channels 1..8, rate 1..MAX_RATE, container bits 8/16/24/32 (PCM) or 32/64 (float)
      * extensible: cbSize >= 22 and inside the chunk, 0 < valid bits <= container bits, and the
        subformat GUID is the standard PCM or IEEE-float one. Valid bits narrower than the
        container are left-justified in it (WAVEFORMATEXTENSIBLE), so the container is decoded
        as full scale and nothing is shifted
      * block_align == channels*width and byte_rate == rate*block_align. A field of 0 is
        tolerated and flagged: it means "unset", sample decoding never uses either field, and
        stdlib wave (the previous decoder) read such files. A nonzero disagreement is corrupt
      * the data payload is capped by MAX_DECODE_BYTES and MAX_DECODE_SAMPLES (declared sizes,
        partial frames included)
    RIFF size: if it is plausible (end >= 36, not 0xFFFFFFFF) it bounds the non-data chunks and
    a data chunk that runs past it by more than its pad byte is a garbage-tail file and excluded
    as unreadable_wav. A zero or 0xFFFFFFFF size (streaming writers) or a size that merely
    disagrees with the file length is tolerated and reported as 'riff_size_mismatch'."""
    head = _read_exact(f, 12)
    if head[:4] != b'RIFF' or head[8:12] != b'WAVE':
        raise WavProblem('unreadable_wav')
    riff_size = struct.unpack('<I', head[4:8])[0]
    riff_end = 8 + riff_size
    plausible = riff_size != 0xFFFFFFFF and riff_end >= 36
    limit = min(file_size, riff_end) if plausible else file_size
    flags = []
    if not (riff_end <= file_size <= riff_end + 1):
        flags.append('riff_size_mismatch')
    pos, fmt = 12, None
    for _ in range(_MAX_CHUNKS):
        if pos + 8 > limit:
            raise WavProblem('unreadable_wav')
        f.seek(pos)
        cid, size = struct.unpack('<4sI', _read_exact(f, 8))
        body = pos + 8
        if cid == b'data':
            if fmt is None:
                raise WavProblem('unreadable_wav')
            kind, ch, width, rate = fmt
            if size > MAX_DECODE_BYTES or -(-size // width) > MAX_DECODE_SAMPLES:
                raise WavProblem('wav_too_large')
            if size > file_size - body:
                raise WavProblem('unreadable_wav')
            if plausible and body + size > riff_end + (size & 1):
                raise WavProblem('unreadable_wav')
            return kind, ch, width, rate, body, size, flags
        if size > limit - body:
            raise WavProblem('unreadable_wav')
        if cid == b'fmt ':
            if fmt is not None or size < 16 or size > _MAX_FMT_BYTES:
                raise WavProblem('unreadable_wav')
            raw = _read_exact(f, size)
            tag, ch, rate, byte_rate, align, bits = struct.unpack('<HHIIHH', raw[:16])
            if size >= 18 and 18 + struct.unpack('<H', raw[16:18])[0] > size:
                raise WavProblem('unsupported_wav_format')
            if tag == 0xFFFE:
                if size < 40 or struct.unpack('<H', raw[16:18])[0] < 22 or raw[26:40] != _PCM_GUID_TAIL:
                    raise WavProblem('unsupported_wav_format')
                valid = struct.unpack('<H', raw[18:20])[0]
                if not 0 < valid <= bits:
                    raise WavProblem('unsupported_wav_format')
                tag = struct.unpack('<H', raw[24:26])[0]
            if tag == 1 and bits in (8, 16, 24, 32):
                kind = 'pcm'
            elif tag == 3 and bits in (32, 64):
                kind = 'float'
            else:
                raise WavProblem('unsupported_wav_format')
            if not (1 <= ch <= MAX_CHANNELS and 1 <= rate <= MAX_RATE):
                raise WavProblem('unsupported_wav_format')
            width = bits // 8
            if align not in (0, ch * width) or byte_rate not in (0, rate * ch * width):
                raise WavProblem('unsupported_wav_format')
            if align == 0 or byte_rate == 0:
                flags.append('fmt_derived_fields_zero')
            fmt = (kind, ch, width, rate)
        pos = body + size + (size & 1)
    raise WavProblem('unreadable_wav')


def _to_float(raw, kind, width):
    """One chunk of whole frames to float64 samples in [-1, 1) with at most one temporary."""
    import numpy as np
    if kind == 'float':
        return np.frombuffer(raw, dtype='<f4' if width == 4 else '<f8').astype(np.float64)
    if width == 1:
        x = np.frombuffer(raw, dtype=np.uint8).astype(np.float64)
        x -= 128.0
        x *= 1.0 / 128.0
    elif width == 2:
        x = np.frombuffer(raw, dtype='<i2').astype(np.float64)
        x *= 1.0 / 32768.0
    elif width == 3:
        buf = np.zeros((len(raw) // 3, 4), dtype=np.uint8)  # left-justify 24 bits in an int32
        buf[:, 1:] = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        x = buf.view('<i4').reshape(-1).astype(np.float64)
        x *= 1.0 / 2147483648.0
    else:
        x = np.frombuffer(raw, dtype='<i4').astype(np.float64)
        x *= 1.0 / 2147483648.0
    return x


def decode_wav_prefix(path, max_seconds=MAX_ANALYSIS_S, flags=None):
    """Decode PCM (8/16/24/32-bit) and IEEE float (32/64-bit) WAV, plain or WAVE_FORMAT_EXTENSIBLE.

    Returns (mono float64 array of at most max_seconds, rate, sha256, total_frames); max_seconds
    None decodes everything. The file is read in 1 MiB chunks: the hash is updated with every
    declared data byte (including a trailing partial frame, so files that differ only there are
    not duplicates) while only the analysed prefix is converted, so the decoded footprint is
    bounded by MAX_DECODE_SAMPLES and the prefix. Float payloads are checked for NaN/Inf over the
    whole payload (WavProblem 'nonfinite_samples'). The hash is sha256 of the prefix string
    '{ch}:{width}:{rate}:' (integer PCM, unchanged from the stdlib-wave version) or
    'f:{ch}:{width}:{rate}:' (float, so the two never collide) followed by the data bytes.
    Channels are mixed by scaling each by 1/ch before summing. Raises WavProblem."""
    import numpy as np
    with open(path, 'rb') as f:
        kind, ch, width, rate, offset, size, hdr_flags = parse_wav_header(f, os.fstat(f.fileno()).st_size)
        if flags is not None:
            flags.extend(hdr_flags)
        frame = ch * width
        total = size // frame
        want = total if max_seconds is None else min(total, int(max_seconds * rate))
        out = np.empty(want, dtype=np.float64)
        prefix = f'{ch}:{width}:{rate}:' if kind == 'pcm' else f'f:{ch}:{width}:{rate}:'
        h = hashlib.sha256(prefix.encode())
        step = max(1, _READ_CHUNK_BYTES // frame) * frame
        f.seek(offset)
        done, left = 0, size
        while left > 0:
            raw = f.read(min(step, left))
            if not raw:
                raise WavProblem('unreadable_wav')
            left -= len(raw)
            h.update(raw)
            whole = raw[:len(raw) // frame * frame]
            if kind == 'float' and whole:
                if not np.isfinite(np.frombuffer(whole, dtype='<f4' if width == 4 else '<f8')).all():
                    raise WavProblem('nonfinite_samples')
            if done < want and whole:
                n = min(len(whole) // frame, want - done)
                x = _to_float(whole[:n * frame], kind, width)
                if ch > 1:
                    with np.errstate(all='ignore'):
                        x = (x.reshape(-1, ch) * (1.0 / ch)).sum(axis=1)
                out[done:done + n] = x
                done += n
                del x
    if done != want:
        raise WavProblem('unreadable_wav')
    if not np.isfinite(out).all():
        raise WavProblem('nonfinite_samples')
    return out, rate, h.hexdigest(), total


def decode_wav(path, max_seconds=None):
    """decode_wav_prefix without the frame count; decodes the whole file unless max_seconds is set."""
    x, rate, digest, _ = decode_wav_prefix(path, max_seconds)
    return x, rate, digest


def signal_measures(x, rate, frame=1024, hop=512, min_gap_s=0.05, max_seconds=MAX_ANALYSIS_S,
                    total_frames=None):
    """Signal measurements only: duration, energy-flux onset count, spectral centroid.

    duration is the full length (total_frames / rate when the caller decoded only a prefix, else
    len(x) / rate); onsets and centroid use only the first max_seconds.
    Onsets: half-wave-rectified rise of frame RMS; a peak counts when it exceeds mean + 2 std of
    the flux (and > 1e-3 absolute), with at least min_gap_s between peaks. Centroid: magnitude-
    weighted mean frequency of the analysed part's FFT. Silent or empty input gives centroid None.
    """
    import numpy as np
    duration = (len(x) if total_frames is None else total_frames) / rate
    x = x[:int(max_seconds * rate)]
    if len(x) == 0 or not np.any(x):
        return duration, 0, None
    peak = float(np.abs(x).max())
    if peak > 1e100:  # squares would overflow; onsets and centroid are scale-invariant here
        x = x / peak
    with np.errstate(all='ignore'):
        return _measure(x, rate, duration, frame, hop, min_gap_s)


def _measure(x, rate, duration, frame, hop, min_gap_s):
    import numpy as np
    n_frames = max(1, 1 + (len(x) - frame) // hop) if len(x) >= frame else 1
    pad = np.pad(x, (0, max(0, frame + hop * (n_frames - 1) - len(x))))
    rms = np.array([np.sqrt(np.mean(pad[i * hop:i * hop + frame] ** 2)) for i in range(n_frames)])
    flux = np.maximum(np.diff(rms, prepend=0.0), 0.0)
    thr = max(flux.mean() + 2.0 * flux.std(), 1e-3)
    gap = max(1, int(round(min_gap_s * rate / hop)))
    onsets, last = 0, -gap
    for i in range(len(flux)):
        left = flux[i - 1] if i > 0 else 0.0
        right = flux[i + 1] if i + 1 < len(flux) else 0.0
        if flux[i] > thr and flux[i] >= left and flux[i] >= right and i - last >= gap:
            onsets += 1
            last = i
    mag = np.abs(np.fft.rfft(x))
    freqs = np.fft.rfftfreq(len(x), 1.0 / rate)
    centroid = float((freqs * mag).sum() / mag.sum()) if mag.sum() > 0 else None
    if centroid is not None and not np.isfinite(centroid):
        centroid = None
    if not np.isfinite(onsets):
        onsets = 0
    return duration, onsets, centroid


def measure_rows(outdir, rows, checks=None):
    """Fill pcm_sha256, duration_s, onset_count, centroid_hz for extracted rows; flag PCM dupes.
    A file that cannot be measured is excluded with a reason; the run never aborts on one.
    Non-fatal header oddities (e.g. riff_size_mismatch) are counted into the optional `checks`
    Counter as measurement flags; they never exclude a file."""
    seen = {}
    for r in rows:
        if not r['include']:
            continue
        flags = []
        try:
            x, rate, digest, frames = decode_wav_prefix(Path(outdir) / r['archive_path'], flags=flags)
            dur, onsets, centroid = signal_measures(x, rate, total_frames=frames)
        except WavProblem as exc:
            r['include'], r['reason'] = False, exc.reason
            continue
        except (EOFError, ValueError, OSError, MemoryError):
            r['include'], r['reason'] = False, 'unreadable_wav'
            continue
        finally:
            x = None  # release this file's samples before the next one is decoded
        if checks is not None:
            checks.update(flags)
        r['pcm_sha256'] = digest
        r['duration_s'] = f'{dur:.4f}'
        r['onset_count'] = onsets
        r['centroid_hz'] = '' if centroid is None else f'{centroid:.1f}'
        if digest in seen:
            r['include'], r['reason'] = False, 'duplicate_pcm_sha256'
            r['duplicate_of'] = seen[digest]
        else:
            seen[digest] = r['file_id']
    return rows


def extract(archive, manifest_path, outdir, unrar='unrar', out_manifest=None, in_place=False,
            private_base=PRIVATE_ROOT, kits_base=KITS_ROOT, max_total=MAX_TOTAL_BYTES):
    if in_place and out_manifest:
        raise ManifestError('--in-place and --out-manifest are mutually exclusive')
    manifest_path = require_under(manifest_path, private_base, 'manifest path')
    dest = manifest_path if in_place else Path(
        out_manifest or manifest_path.with_name(manifest_path.name + '.extracted.csv'))
    require_under(dest, private_base, 'output manifest path')
    rows = read_manifest(manifest_path)
    todo = plan_extraction(rows)
    if any(r['archive_name'] != Path(archive).name for r in todo):
        raise ManifestError('archive file name does not match the manifest archive_name')
    check_unrar_version(unrar)
    check_budget(todo, require_under(outdir, kits_base, 'output dir'), max_total)
    out = prepare_outdir(outdir, kits_base)
    if todo:
        rc, so, se = run_unrar(unrar, Path(archive).resolve(), [r['archive_path'] for r in todo], out)
        if rc != 0:
            raise ManifestError(f'unrar exited {rc}; output dir left for inspection. '
                                f'stderr: {safe_text(se)[:500]}')
    stray = unexpected_entries(out, todo)
    if stray:
        raise ManifestError(f'{len(stray)} entries in the output dir were not in the extraction plan; '
                            f'left for inspection, e.g. {safe_text(stray[0])[:120]}')
    before = {r['file_id'] for r in rows if r['include']}
    for r in todo:
        why = verify_extracted(out, r)
        if why:
            r['include'], r['reason'] = False, why
    checks = Counter()
    measure_rows(out, rows, checks)
    # verified_label is deliberately left empty: no signal rule here identifies a sound type.
    written = write_manifest(rows, dest, private_base)
    newly = Counter(r['reason'] for r in rows if not r['include'] and r['file_id'] in before)
    kept = sum(1 for r in rows if r['include'])
    print(f'extracted and measured; {kept} rows still included; manifest written to {written}')
    for reason, n in sorted(newly.items()):
        print(f'  excluded during extract, {reason}: {n}')
    for flag, n in sorted(checks.items()):
        print(f'  measurement flag (not an exclusion), {flag}: {n}')
    return rows


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = p.add_subparsers(dest='cmd', required=True)
    m = sub.add_parser('manifest', help='build manifest.csv from a listing')
    m.add_argument('--listing', required=True, help='text output of `unrar lt` or `7z l -slt`')
    m.add_argument('--archive-name', required=True)
    m.add_argument('--acquisition', help='CSV mapping kit,status (purchased/free/shared/unverified)')
    g = m.add_mutually_exclusive_group(required=True)
    g.add_argument('--recycled-kits', help='private file (under data/private/): kits admitting '
                   'recycled sounds, one per line, comma-separated words')
    g.add_argument('--no-recycled-kits', action='store_true',
                   help='explicitly do not exclude any recycled/collated kit')
    m.add_argument('--max-file-bytes', type=int, default=MAX_FILE_BYTES)
    m.add_argument('--verbose', action='store_true', help='also print (escaped) member paths')
    m.add_argument('out', help='manifest.csv path, must be under data/private/')
    e = sub.add_parser('extract', help='extract include=true rows and measure them')
    e.add_argument('--archive', required=True)
    e.add_argument('--manifest', required=True)
    e.add_argument('--unrar', default='unrar')
    e.add_argument('--max-total-bytes', type=int, default=MAX_TOTAL_BYTES)
    eg = e.add_mutually_exclusive_group()
    eg.add_argument('--out-manifest', help='default: <manifest>.extracted.csv')
    eg.add_argument('--in-place', action='store_true', help='overwrite the source manifest')
    e.add_argument('outdir', help='fresh dir under data/private/kits/ (created by this command)')
    args = p.parse_args(argv)
    try:
        if args.cmd == 'manifest':
            require_under(args.out, PRIVATE_ROOT, 'manifest path')
            try:
                text = Path(args.listing).read_text(encoding='utf-8', errors='strict')
            except UnicodeDecodeError:
                raise ManifestError('listing is not valid UTF-8; re-list with LC_ALL=C.UTF-8')
            acq = read_acquisition(args.acquisition) if args.acquisition else None
            recycled = read_recycled(args.recycled_kits) if args.recycled_kits else ()
            rows, recon = build_rows(parse_listing(text), args.archive_name, acq, recycled,
                                     args.max_file_bytes, args.recycled_kits)
            print(format_reconciliation(recon, args.verbose))
            print(f'wrote {write_manifest(rows, args.out, PRIVATE_ROOT)}')
        else:
            extract(args.archive, args.manifest, args.outdir, args.unrar, args.out_manifest,
                    args.in_place, max_total=args.max_total_bytes)
    except ManifestError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
