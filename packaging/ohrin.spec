# PyInstaller spec for the Ohrin desktop app (onedir).
# Build: pyinstaller packaging/ohrin.spec --noconfirm --distpath dist --workpath build
# Set OHRIN_FREEZE_GTK=0 on Linux to leave out gi (browser-only frozen build).
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent

excludes = ["experiments", "scripts", "tkinter"]
hiddenimports = []
if sys.platform.startswith("linux") and os.environ.get("OHRIN_FREEZE_GTK", "1") == "0":
    excludes += ["gi", "cairo"]
elif sys.platform.startswith("linux"):
    hiddenimports += ["gi"]

a = Analysis(
    [str(ROOT / "packaging" / "ohrin_entry.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "web"), "web")],
    hiddenimports=hiddenimports,
    excludes=excludes,
)
if sys.platform.startswith("linux"):
    # The GTK hook copies every system icon theme (about 1.3 GB). Keep only the fallback themes.
    def _keep(dest):
        parts = Path(dest).parts
        if parts[:2] == ("share", "icons"):
            return len(parts) < 3 or parts[2] in ("hicolor", "Adwaita")
        return parts[:2] != ("share", "themes")
    a.datas = [d for d in a.datas if _keep(d[0])]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ohrin",
    console=sys.platform.startswith("linux"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="Ohrin")

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Ohrin.app",
        bundle_identifier="io.github.OWNER.ohrin",  # placeholder: owner chooses
    )
