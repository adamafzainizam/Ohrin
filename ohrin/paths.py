"""Where Ohrin keeps user data and finds its bundled resources. Nothing is created here."""

import os
from pathlib import Path
import sys


def user_data_dir(env=os.environ, platform=sys.platform, home=None):
    home = Path.home() if home is None else Path(home)
    override = env.get("OHRIN_DATA_DIR")
    if override:
        return Path(override)
    if platform == "darwin":
        return home / "Library" / "Application Support" / "Ohrin"
    if platform == "win32":
        base = env.get("LOCALAPPDATA")
        return Path(base) / "Ohrin" if base else home / "AppData" / "Local" / "Ohrin"
    xdg = env.get("XDG_DATA_HOME", "")
    if xdg and Path(xdg).is_absolute():
        return Path(xdg) / "Ohrin"
    return home / ".local" / "share" / "Ohrin"


def vggish_dir():
    return user_data_dir() / "vggish"


def models_dir():
    return user_data_dir() / "models"


def webview_storage_dir():
    return user_data_dir() / "webview"


def log_dir():
    return user_data_dir() / "logs"


def is_frozen():
    return getattr(sys, "frozen", False)


def resource_root(frozen=None, meipass=None):
    if frozen is None:
        frozen = is_frozen()
    if frozen:
        return Path(meipass if meipass is not None else sys._MEIPASS)
    return Path(__file__).resolve().parent.parent
