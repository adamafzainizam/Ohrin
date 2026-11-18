"""Locate FFmpeg and ffprobe. They are required but not bundled."""

import os
from pathlib import PureWindowsPath, PurePosixPath
import shutil
import subprocess
import sys


class FFmpegMissing(ValueError):
    pass


def install_hint(platform=None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return "winget install Gyan.FFmpeg"
    if platform == "darwin":
        return "brew install ffmpeg"
    return "sudo apt install ffmpeg (or your distribution's package)"


def known_dirs(platform, env):
    if platform == "darwin":
        return [PurePosixPath(p) for p in ("/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin")]
    if platform == "win32":
        W = PureWindowsPath
        dirs = []
        if env.get("LOCALAPPDATA"):
            dirs.append(W(env["LOCALAPPDATA"]) / "Microsoft" / "WinGet" / "Links")
        dirs.append(W("C:/ffmpeg/bin"))
        if env.get("ProgramFiles"):
            dirs.append(W(env["ProgramFiles"]) / "ffmpeg" / "bin")
        if env.get("USERPROFILE"):
            dirs.append(W(env["USERPROFILE"]) / "scoop" / "shims")
        dirs.append(W("C:/ProgramData/chocolatey/bin"))
        return dirs
    return [PurePosixPath(p) for p in ("/usr/bin", "/usr/local/bin", "/snap/bin")]


def find_tool(name, *, env=None, platform=None, which=shutil.which, is_file=os.path.isfile):
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    filename = name + ".exe" if platform == "win32" else name
    Pure = PureWindowsPath if platform == "win32" else PurePosixPath
    override = env.get("OHRIN_FFMPEG_DIR")
    if override:
        candidate = str(Pure(override) / filename)
        if is_file(candidate):
            return {"path": candidate, "source": "env"}
    found = which(name)
    if found:
        return {"path": found, "source": "path"}
    for directory in known_dirs(platform, env):
        candidate = str(directory / filename)
        if is_file(candidate):
            return {"path": candidate, "source": "known"}
    return None


def status(**kwargs):
    """Re-resolves on every call so "Check again" sees a fresh install."""
    ffmpeg = find_tool("ffmpeg", **kwargs)
    ffprobe = find_tool("ffprobe", **kwargs)
    return {
        "found": bool(ffmpeg and ffprobe),
        "ffmpeg": ffmpeg["path"] if ffmpeg else None,
        "ffprobe": ffprobe["path"] if ffprobe else None,
        "hint": install_hint(kwargs.get("platform")),
    }


def tool(name):
    found = find_tool(name)
    if not found:
        raise FFmpegMissing(f"FFmpeg ({name}) was not found. Install it with: {install_hint()}")
    return found["path"]


def run_kwargs(platform=None):
    platform = sys.platform if platform is None else platform
    if platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}
