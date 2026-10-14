#!/usr/bin/env python3
"""Fetch the two VGGish weight files Ohrin needs, verifying SHA-256.

The files come from their upstream sources and are not redistributed in this
repository. Stdlib only. Usage:

    python3 scripts/fetch_vggish.py [--dest data/vggish] [--force]
"""
import argparse
import hashlib
import http.client
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHUNK = 1024 * 1024
TIMEOUT_S = 60
SIZE_SLACK = 1024 * 1024

FILES = [
    {
        "name": "vggish-10086976.pth",
        "url": "https://github.com/harritaylor/torchvggish/releases/download/v0.1/vggish-10086976.pth",
        "sha256": "10086976245803799d9194e9a73d9b6c1549c71d1b80106f5cade5608a561f4b",
        "size": 288567937,
    },
    {
        "name": "vggish_pca_params.npz",
        "url": "https://storage.googleapis.com/audioset/vggish_pca_params.npz",
        "sha256": "4d878af3e306defbfb37095365aa7e5a6cecb86bad13e859e626151c4e6a8b9d",
        "size": 73020,
    },
]


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


def verify_or_download(url, dest, sha256, opener=urllib.request.urlopen, force=False, progress=None, max_bytes=None):
    """Return "cached" or "downloaded". Never leaves a wrong file at `dest`.

    Downloads go to `<dest>.part` and are renamed only after the checksum matches;
    `max_bytes` aborts the stream early. A failed run leaves any existing file alone."""
    dest = Path(dest)
    if dest.exists():
        if sha256_file(dest) == sha256:
            return "cached"
        if not force:
            raise ExistingFileInvalid(f"{dest} exists with an unexpected checksum; re-run with --force to replace it")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(url, timeout=TIMEOUT_S) as response, open(part, "wb") as out:
            declared = getattr(getattr(response, "headers", None), "get", lambda *_: None)("Content-Length")
            if max_bytes is not None and declared and declared.isdigit() and int(declared) > max_bytes:
                raise SizeExceeded(f"server announced {declared} bytes, more than the expected {max_bytes}; aborting")
            while True:
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch verified VGGish weights for Ohrin")
    parser.add_argument("--dest", default=str(ROOT / "data" / "vggish"))
    parser.add_argument("--force", action="store_true", help="replace an existing file with a wrong checksum")
    args = parser.parse_args(argv)
    dest = Path(args.dest)
    for item in FILES:
        target = dest / item["name"]
        def progress(done, size=item["size"]):
            sys.stderr.write(f"\r{item['name']}: {done / 1e6:.1f} / {size / 1e6:.1f} MB")
            sys.stderr.flush()

        try:
            state = verify_or_download(item["url"], target, item["sha256"], force=args.force, progress=progress,
                                      max_bytes=item["size"] + SIZE_SLACK)
        except (ChecksumMismatch, ExistingFileInvalid, SizeExceeded) as exc:
            sys.stderr.write("\n")
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except (OSError, http.client.HTTPException) as exc:
            sys.stderr.write("\n")
            print(f"error: download failed: {exc}", file=sys.stderr)
            return 1
        if state != "cached":
            sys.stderr.write("\r" + " " * 70 + "\r")
        print(f"{item['name']}: ok (cached)" if state == "cached" else f"{item['name']}: ok (downloaded, checksum verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
