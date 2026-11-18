"""Verified downloads of the model files Ohrin needs. Stdlib only, no network use except when a download is asked for."""
import hashlib
import os
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

CHUNK = 1024 * 1024
TIMEOUT_S = 20
STALE_PART_AGE_S = 3600
SIZE_SLACK = 1024 * 1024


HEAD_URL = None  # set to the GitHub Release download URL once the owner publishes the trained head

ASSETS = [
    {
        "name": "vggish-10086976.pth",
        "subdir": "vggish",
        "url": "https://github.com/harritaylor/torchvggish/releases/download/v0.1/vggish-10086976.pth",
        "sha256": "10086976245803799d9194e9a73d9b6c1549c71d1b80106f5cade5608a561f4b",
        "size": 288567937,
        "host": "github.com (release download, served from githubusercontent.com)",
    },
    {
        "name": "vggish_pca_params.npz",
        "subdir": "vggish",
        "url": "https://storage.googleapis.com/audioset/vggish_pca_params.npz",
        "sha256": "4d878af3e306defbfb37095365aa7e5a6cecb86bad13e859e626151c4e6a8b9d",
        "size": 73020,
        "host": "storage.googleapis.com",
    },
    {
        "name": "openmic-mlp.pt",
        "subdir": "models",
        "url": HEAD_URL,
        "sha256": "aa8dc0e5610067fd211393b126cb979ba895d7b61f417a7ddc858cfe2bd70d3d",
        "size": 36805,
        "host": "github.com (release download, served from githubusercontent.com)",
    },
]

class Cancelled(RuntimeError):
    pass


class ChecksumMismatch(RuntimeError):
    pass


class ExistingFileInvalid(RuntimeError):
    pass


class SizeExceeded(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_or_download(url, dest, sha256, opener=urllib.request.urlopen, force=False, progress=None, max_bytes=None, cancel=None):
    """Return "cached" or "downloaded". Never leaves a wrong file at `dest`.

    Downloads go to a unique `<dest>.<random>.part` created exclusively beside `dest` and are renamed only after the checksum matches;
    `max_bytes` aborts the stream early; `cancel` is polled once per chunk. A failed run leaves any existing file alone."""
    dest = Path(dest)
    if dest.exists():
        if sha256_file(dest) == sha256:
            return "cached"
        if not force:
            raise ExistingFileInvalid(f"{dest} exists with an unexpected checksum; re-run with --force to replace it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, part_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
    part = Path(part_name)
    digest = hashlib.sha256()
    done = 0
    try:
        with os.fdopen(fd, "wb") as out, opener(url, timeout=TIMEOUT_S) as response:
            declared = getattr(getattr(response, "headers", None), "get", lambda *_: None)("Content-Length")
            if max_bytes is not None and declared and declared.isdigit() and int(declared) > max_bytes:
                raise SizeExceeded(f"server announced {declared} bytes, more than the expected {max_bytes}; aborting")
            while True:
                if cancel is not None and cancel():
                    raise Cancelled("download cancelled")
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                done += len(chunk)
                if max_bytes is not None and done > max_bytes:
                    raise SizeExceeded(f"download exceeded the expected size of {max_bytes} bytes; aborting")
                out.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(done)
        if digest.hexdigest() != sha256:
            raise ChecksumMismatch(f"checksum mismatch: expected {sha256}, got {digest.hexdigest()}")
        os.replace(part, dest)
    finally:
        if part.exists():
            part.unlink()
    return "downloaded"


def sweep_stale_parts(data_dir, max_age=STALE_PART_AGE_S):
    """Delete leftover `*.part` download files older than `max_age` seconds in the asset directories. Nothing else."""
    cutoff = time.time() - max_age
    for subdir in sorted({e["subdir"] for e in ASSETS} | {"vggish", "models"}):
        folder = Path(data_dir) / subdir
        try:
            candidates = list(folder.glob("*.part"))
        except OSError:
            continue
        for path in candidates:
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                pass


_VERIFIED = {}
_VERIFIED_LOCK = threading.Lock()


def _is_verified(path, expected):
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, expected)
    with _VERIFIED_LOCK:
        if key in _VERIFIED:
            return _VERIFIED[key]
    result = sha256_file(path) == expected
    with _VERIFIED_LOCK:
        for old in [k for k in _VERIFIED if k[0] == key[0]]:
            del _VERIFIED[old]
        _VERIFIED[key] = result
    return result


def asset_status(data_dir):
    result = []
    for entry in ASSETS:
        path = Path(data_dir) / entry["subdir"] / entry["name"]
        present = path.is_file()
        result.append({
            "name": entry["name"],
            "present": present,
            "verified": present and _is_verified(path, entry["sha256"]),
            "bytes": path.stat().st_size if present else 0,
            "size": entry["size"],
        })
    return result


def missing(data_dir):
    status = {s["name"]: s for s in asset_status(data_dir)}
    return [e for e in ASSETS if not status[e["name"]]["verified"]]


def downloadable(data_dir):
    return all(e["url"] for e in missing(data_dir))
