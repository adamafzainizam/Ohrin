# Third-party notices

Ohrin itself is Apache-2.0 (see `LICENSE`). This file lists what the packaged desktop app bundles or fetches. Licence names below were read from the licence metadata and files of the packages as installed on the build machine (`~/.venvs/ohrin-ml` and the Linux bundle under `dist/Ohrin/_internal/`, checked 2026-11-18). Items marked "check upstream" were not read from a local file, so no licence is asserted for them. Each bundled package's own licence text travels with its `dist-info` folder where the build includes one.

## Bundled Python packages (read from installed metadata)

| Package | Version | Licence as declared | Notes |
| --- | --- | --- | --- |
| torch | 2.15.0+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib | Licence files for its vendored third-party code ship in its `dist-info`, which is present in the Linux bundle. |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | `dist-info` present in the Linux bundle. |
| MarkupSafe | 3.0.3 | BSD-3-Clause | Pulled in by the torch build; `dist-info` present in the Linux bundle. |
| pywebview | 6.2.1 | BSD 3-Clause License | Code bundled; its `dist-info` is not copied into the bundle, see the installed `LICENSE`. |
| bottle | 0.13.4 | MIT | As above. |
| proxy_tools | 0.1.0 | MIT (metadata only; the package ships no licence file) | As above. |
| typing_extensions | 4.16.0 | PSF-2.0 | As above. |
| setuptools | 84.2.1 | MIT | Present in the Linux bundle as a package. |

The Windows build also bundles pythonnet and clr_loader, and the macOS build also bundles pyobjc. They were not installed on this machine, so their licences are not read here: check upstream.

## Build tool

PyInstaller 6.22.3 builds the app. Its metadata declares "GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs", and `COPYING.txt` contains a "Bootloader Exception" giving permission to link or embed the compiled bootloader into the built program. The bootloader is part of the packaged app. The bundle is a onedir layout, so libraries stay separate files. pyinstaller-hooks-contrib 2026.10 (its metadata lists Apache Software License and GPLv2 classifiers) and altgraph 0.17.5 (MIT) are build-time only.

## Linux GTK stack (check upstream)

The Linux bundle was built with `gi` (PyGObject) and contains, among others, `libgtk-3.so.0`, `libcairo.so.2`, `libcairo-gobject.so.2` and `libgirepository-1.0.so.1` copied from the build machine's system packages. These GTK, GLib, cairo and GObject-Introspection libraries come from the build host. WebKitGTK is not included: it is the system webview, provided by the user's machine and used through `gir1.2-webkit2-4.1`. The licence texts for PyGObject, GTK, cairo, GObject Introspection and WebKitGTK were not read from local files for this notice: check upstream (https://pygobject.gnome.org, https://www.gtk.org, https://www.cairographics.org, https://webkitgtk.org). The Linux build keeps these as separate shared-library files rather than linking them into one executable.

## Fonts and icons in `web/`

- Geist fonts: SIL Open Font License 1.1, text in `web/fonts/OFL.txt` (copyright 2026 The Geist Project Authors).
- Phosphor icons: MIT, text in `web/icons/LICENSE` (copyright 2023 Phosphor Icons).

## Not bundled

- **Webviews on Windows and macOS** (WebView2 / WKWebView) come from the operating system and are not redistributed.
- **FFmpeg** is not bundled. The app uses an FFmpeg you install yourself; its licence depends on the build you install.
- **VGGish weights and AudioSet PCA parameters** are downloaded at runtime from their upstream sources after you consent, and are not redistributed in the app or in this repository. Sources: https://github.com/harritaylor/torchvggish and https://github.com/tensorflow/models/tree/master/research/audioset/vggish. Licence text: check upstream.
- **OpenMIC-2018** is the dataset used to train the instrument head. It is not bundled. Source: https://zenodo.org/record/1432913. Its archive lists a `license-cc-by.txt` member that has not been read for this notice: check upstream.
- **Instrument head.** No trained head is published yet. Instrument candidates need a head that will be published later. If one is released, its attribution text will be taken from the OpenMIC licence file and added here.
