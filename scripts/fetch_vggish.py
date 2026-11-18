#!/usr/bin/env python3
"""Fetch the two VGGish weight files Ohrin needs, verifying SHA-256.

The files come from their upstream sources and are not redistributed in this
repository. Stdlib only. Usage:

    python3 scripts/fetch_vggish.py [--dest data/vggish] [--force]
"""
import argparse
import http.client
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if not __package__:
    sys.path.insert(0, str(ROOT))

from ohrin.assets import (  # noqa: E402,F401  (re-exported so callers and tests can patch them here)
    ASSETS, CHUNK, SIZE_SLACK, TIMEOUT_S, Cancelled, ChecksumMismatch, ExistingFileInvalid, SizeExceeded,
    sha256_file, verify_or_download,
)

FILES = [{k: e[k] for k in ("name", "url", "sha256", "size")} for e in ASSETS if e["subdir"] == "vggish"]


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
