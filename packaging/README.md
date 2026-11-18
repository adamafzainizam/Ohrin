# Packaging

Build a onedir app from the venv that has torch, pywebview and pyinstaller:

    pyinstaller packaging/ohrin.spec --noconfirm --distpath dist --workpath build

Output goes to the gitignored `dist/` and `build/`. On Linux, `OHRIN_FREEZE_GTK=0`
leaves out `gi` for a browser-only build. FFmpeg is detected, not bundled.
Check the result with `dist/Ohrin/Ohrin --self-test --self-test-report st.json`.

## Notes from the Linux build

- A local build from a venv whose `gi` is only symlinked from the system needs PyGObject's package metadata to be visible to PyInstaller's hook. Either `pip install PyGObject` in the venv (it needs the build dependencies listed in the workflow), or put a stub `pygobject-<ver>.dist-info/METADATA` on `PYTHONPATH`.
- Measured size of the Linux onedir bundle: 908 MB, of which torch is about 680 MB. The spec drops GTK icon themes other than hicolor and Adwaita, and the GTK themes, to keep it from growing by over a gigabyte.
