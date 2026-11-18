# Packaging

Build a onedir app from the venv that has torch, pywebview and pyinstaller:

    pyinstaller packaging/ohrin.spec --noconfirm --distpath dist --workpath build

Output goes to the gitignored `dist/` and `build/`. On Linux, `OHRIN_FREEZE_GTK=0`
leaves out `gi` for a browser-only build. FFmpeg is detected, not bundled.
Check the result with `dist/Ohrin/Ohrin --self-test --self-test-report st.json`.
