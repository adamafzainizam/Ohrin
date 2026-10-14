#!/usr/bin/env python3
"""Fetch OpenMIC-2018 and extract only the four files Ohrin's training needs.

Stdlib only. Without --yes nothing is downloaded (the archive is about 2.6 GB).

    python3 scripts/fetch_openmic.py [--dest data/openmic-2018] [--archive PATH] [--yes]
"""
import argparse
import hashlib
import os
import sys
import tarfile
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
URL = "https://zenodo.org/records/1432913/files/openmic-2018-v1.0.0.tgz?download=1"
ARCHIVE_NAME = "openmic-2018-v1.0.0.tgz"
ARCHIVE_BYTES = 2623376754
ARCHIVE_MD5 = "e4ccf187e2bb5ab2e115416e8aafe7f4"
NPZ_SHA256 = "3e5f9252e2f983737d13f87a52b3ee02c8cad357f00531762490b31f18d3e62e"
ALLOWLIST = ("openmic-2018.npz", "class-map.json", "partitions/split01_train.csv", "partitions/split01_test.csv")
CHUNK = 1024 * 1024
TIMEOUT_S = 60


def file_digest(path, algorithm):
    digest = hashlib.new(algorithm)
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url, target, expected_md5, opener=urllib.request.urlopen, progress=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    digest = hashlib.md5()
    done = 0
    try:
        with opener(url, timeout=TIMEOUT_S) as response, open(part, "wb") as out:
            for chunk in iter(lambda: response.read(CHUNK), b""):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done)
        if digest.hexdigest() != expected_md5:
            raise RuntimeError(f"checksum mismatch: expected MD5 {expected_md5}, got {digest.hexdigest()}")
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()


def _relative_name(member_name):
    """Normalised POSIX path, or None for absolute or parent-escaping names."""
    path = PurePosixPath(member_name)
    if path.is_absolute() or ".." in path.parts:
        return None
    return str(path)


def extract_allowlisted(archive, dest):
    """Extract only ALLOWLIST members, from the archive root or one top-level folder."""
    wanted = set(ALLOWLIST)
    top = None
    found = set()
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        for member in tf:
            name = _relative_name(member.name)
            if name is None or not member.isfile():
                continue
            parts = name.split("/")
            if name in wanted:
                rel, prefix = name, ""
            elif len(parts) > 1 and "/".join(parts[1:]) in wanted:
                rel, prefix = "/".join(parts[1:]), parts[0]
            else:
                continue
            if top is not None and prefix != top:
                continue
            top = prefix
            if rel in found:
                continue
            tf.extract(member.replace(name=rel, deep=False), path=dest, filter="data")
            found.add(rel)
            if found == wanted:
                break
    missing = wanted - found
    if missing:
        raise RuntimeError(f"archive is missing expected members: {sorted(missing)}")
    return sorted(found)


def fetch_openmic(dest, archive=None, yes=False, opener=urllib.request.urlopen,
                  expected_md5=ARCHIVE_MD5, expected_npz_sha=NPZ_SHA256, url=URL, out=print):
    dest = Path(dest)
    if archive is None:
        archive = dest.parent / ARCHIVE_NAME
        if not archive.exists():
            if not yes:
                out(f"OpenMIC-2018 is about {ARCHIVE_BYTES / 1e9:.1f} GB ({URL}).")
                out("Nothing was downloaded. Re-run with --yes to download it, or pass --archive PATH.")
                return 0
            out(f"Downloading {ARCHIVE_BYTES / 1e9:.1f} GB to {archive} ...")
            download(url, archive, expected_md5, opener=opener)
    archive = Path(archive)
    if not archive.is_file():
        raise FileNotFoundError(f"archive not found: {archive}")
    digest = file_digest(archive, "md5")
    if digest != expected_md5:
        raise RuntimeError(f"archive checksum mismatch: expected MD5 {expected_md5}, got {digest}")
    names = extract_allowlisted(archive, dest)
    npz_digest = file_digest(dest / "openmic-2018.npz", "sha256")
    if npz_digest != expected_npz_sha:
        raise RuntimeError(f"openmic-2018.npz checksum mismatch: expected {expected_npz_sha}, got {npz_digest}")
    out(f"Extracted {len(names)} files to {dest}; NPZ SHA-256 verified.")
    out(f"The archive was kept at {archive}; delete it yourself if you no longer need it.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch OpenMIC-2018 for Ohrin training")
    parser.add_argument("--dest", default=str(ROOT / "data" / "openmic-2018"))
    parser.add_argument("--archive", help="use an already-downloaded openmic-2018-v1.0.0.tgz")
    parser.add_argument("--yes", action="store_true", help="confirm the ~2.6 GB download")
    args = parser.parse_args(argv)
    try:
        return fetch_openmic(args.dest, archive=args.archive, yes=args.yes)
    except (RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
