# Ohrin

[![CI](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml/badge.svg)](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml)

Ohrin takes its name from the German "Ohren", meaning "ears".

It is a local, open-source Python/ML tool with a browser UI for guitarists learning by ear and producers studying how a track is arranged. You pick a passage, loop and slow it, see what changes in level and texture, and get cautious candidates for pulse, chords and instruments that you can correct and export as a private note.

Status: local prototype. The workbench, signal analysis, note export (schema v3), a seven-band listening EQ, a four-class instrument model trained on OpenMIC, a per-block instrument timeline and an in-session listening review all work. The model has held-out results on OpenMIC clips only. Its behaviour on full commercial mixes has not been measured, so the app shows its scores as uncertain model output.

Ohrin is meant to be installed and run on your own machine, not hosted. A packaged desktop app exists in draft: a Linux build has been made and checked locally on one machine (self-test only). A build workflow for Windows and macOS is written but has not been run, so those builds are untested. No releases have been published yet. See [Install the app](#install-the-app).

## Start here

[docs/concept.md](docs/concept.md) holds the longer vision and the first-version baseline. [docs/model-card.md](docs/model-card.md) records the model, its evaluation and its limits.

## Next steps

- Check how the instrument heads transfer to full mixes, using audio you are allowed to label and share.
- Run the build workflow, check the Windows and macOS builds and publish a first release.
- Improve pulse and chord candidates, which are the weakest measured parts.

## Install the app

Status: no release exists yet. The Linux build was made and self-tested locally. The Windows and macOS builds come from `.github/workflows/build.yml`, which has not been run, so their GUIs are unverified. The apps are unsigned and there is no update check: look at GitHub Releases for new versions.

Get a build from the artifacts of a run of the "Build desktop app" workflow in the Actions tab or, once there is one, from Releases.

Unsigned apps need a manual first open:

- macOS: System Settings, Privacy & Security, Open Anyway. Or run `xattr -dr com.apple.quarantine Ohrin.app`.
- Windows: SmartScreen, More info, Run anyway.

FFmpeg is required for analysis and is not bundled. Install it yourself: `sudo apt install ffmpeg` on Debian or Ubuntu, `brew install ffmpeg` on macOS, or `winget install Gyan.FFmpeg` on Windows (the in-app "Check again" button finds it without a restart; if you edit PATH by hand instead, restart the app). Without it the app opens but analysis stays disabled.

First run: no trained instrument head is published yet, so a fresh install downloads nothing and shows instrument candidates as unavailable. Once a head is published, the app will ask before downloading the VGGish weights (about 289 MB, from a GitHub release), the AudioSet PCA parameters (about 73 KB, from storage.googleapis.com) and the head, with progress and a cancel button. It will download nothing without your consent.

Linux runtime requirements: the bundle uses the system WebKitGTK (`libwebkit2gtk-4.1-0` / `gir1.2-webkit2-4.1`); without it the app opens in your browser instead. It also needs a glibc at least as new as the build machine's (CI builds use Ubuntu 24.04, glibc 2.39).

Verification status: the Linux frozen build was opened and used in a window on one Linux Mint 22.3 machine. The Windows and macOS GUIs are unverified, and the CI workflow has not run yet.

Data directories hold downloaded weights and settings: `~/.local/share/Ohrin` on Linux (or `$XDG_DATA_HOME/Ohrin`), `~/Library/Application Support/Ohrin` on macOS, `%LOCALAPPDATA%\Ohrin` on Windows. `OHRIN_DATA_DIR` overrides them.

Run `Ohrin --browser` to use your browser instead of the app window. The server keeps running when you close a browser tab; use Quit in the app. The saved theme is kept as long as port 38765 is free.

Linux from source (needs a GTK/WebKit install):

```bash
sudo apt install ffmpeg gir1.2-webkit2-4.1 libgirepository-2.0-dev libcairo2-dev pkg-config
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-desktop.txt PyGObject
python3 -m ohrin.app
```

An example launcher, saved as `~/.local/share/applications/ohrin.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Ohrin
Exec=/path/to/Ohrin/.venv/bin/python -m ohrin.app
Path=/path/to/Ohrin
Terminal=false
```

To build it yourself, see [packaging/README.md](packaging/README.md).

## Workspace

- `ohrin/`: analysis, VGGish extraction, instrument inference and the local server.
- `web/`: the browser UI (static HTML, CSS and JavaScript).
- `scripts/`: download helpers for model weights and the OpenMIC data.
- `experiments/`: model training and evaluation scripts.
- `docs/`: concept, first-version scope and model card.
- `tests/`: Python and Node tests that use synthetic signals.
- `audio/`, `data/`, `outputs/`: local only and ignored by Git.

Audio and generated outputs stay out of Git by default. A recording you analyse locally is not a demo asset or a training dataset.

## Requirements

- Core workbench: Python 3.12, FFmpeg and NumPy (`requirements.txt`), plus a modern browser.
- Instrument candidates also need PyTorch (`requirements-ml.txt`), the fetched VGGish weights and a trained head in `outputs/`. Without any of these the app still runs and shows an "instrument model unavailable" state with the reason.

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 -m ohrin.server --port 8765
```

Open `http://127.0.0.1:8765`. Choose an audio file under 50 MB, select a passage of up to three minutes by dragging the waveform or entering times, then loop, slow and analyse it. Uploaded bytes go to a temporary file for FFmpeg and are deleted after analysis. The browser plays a local object URL. Notes export as JSON without audio. Listening EQ shapes playback only; analysis uses the original audio. Its frequency presets are creative starting points, not instrument isolation. Bypass skips EQ but keeps output soft clipping. EQ settings can be saved in a passage note and loaded locally; no audio is exported. Listening marks stay in memory for the open tab and appear only in the exported note.

## The workbench

Layout: a transport bar at the top, then a stack of lanes that share one time axis (a whole-track overview, a time ruler, the waveform, the measured RMS level and, after analysis, one lane per instrument with the block strip below). From 1280 px wide the inspector sits beside the lanes; below that it sits under them. It has tabs for Measured, Pulse, Instruments, Guitar, Arrange and Notes. Findings from analysis appear in those tabs. Pulse, chord, instrument and tuning results are candidates, not facts.

Zoom and pan the lanes:

- Ctrl or Cmd plus the mouse wheel zooms around the pointer.
- Shift plus the wheel, or a mostly horizontal wheel or trackpad swipe, pans.
- With the lanes focused, `+` or `=` zooms in and `-` zooms out. These keys are ignored while typing in a field or using the EQ panel.
- The buttons beside the ruler zoom in and out, and "Passage" and "Track" fit the view to the selected passage or the whole track.
- In the overview, click to centre the view there, or drag the view box to pan.
- During playback the view follows the playhead until you zoom or pan, and resumes when you press play again.

In the inspector, Left and Right move between tabs, and Home and End jump to the first and last.

Theme: the System, Light and Dark buttons set the colour theme. They are in the top bar from 1280 px wide and inside the More menu below that. System follows the operating system, and an explicit choice is remembered in the browser's local storage.

Privacy: the server listens on 127.0.0.1 only and has no option to change that. It rejects requests for other hosts, and the cross-origin guard covers every POST. The page sends audio only to that local server, for analysis, and loads no remote scripts, fonts or services. Playback uses a local object URL. The only network calls the app makes are the downloads you consent to in setup.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/app.test.mjs
```

Tests use synthetic signals and need no weights, datasets or audio. PyTorch-dependent tests are skipped when PyTorch is not installed. The workflow in `.github/workflows/ci.yml` runs both suites with and without PyTorch on pull requests.

## Fetch weights and reproduce the model

VGGish weights come from their upstream sources and are not redistributed here: the PyTorch port release from [`harritaylor/torchvggish`](https://github.com/harritaylor/torchvggish) and Google's AudioSet `vggish_pca_params.npz` (see the original [VGGish](https://github.com/tensorflow/models/tree/master/research/audioset/vggish) page in `tensorflow/models`). Check each upstream licence yourself before reuse; this repository does not restate their terms. Downloads are size-limited and checked against recorded SHA-256 values.

```bash
python3 scripts/fetch_vggish.py            # -> data/vggish/
python3 -m pip install 'torch>=2.13.0' --index-url https://download.pytorch.org/whl/cpu   # CPU index only; a plain install pulls the much larger CUDA build on Linux
python3 -m pip install -r requirements-ml.txt   # remaining requirements from PyPI
python3 scripts/fetch_openmic.py --yes     # ~2.6 GB; extracts four files to data/openmic-2018/
python3 -m experiments.train_openmic --data-root data/openmic-2018   # writes outputs/openmic-{linear,mlp}.pt + metrics
python3 -m ohrin.server
```

Without `--yes`, `fetch_openmic.py` only prints the download size, unless the archive is already cached at `data/openmic-2018-v1.0.0.tgz`, in which case it extracts it. Use `--archive PATH` for an already downloaded `openmic-2018-v1.0.0.tgz` (MD5 `e4ccf187e2bb5ab2e115416e8aafe7f4`). OpenMIC-2018 has its own licence and attribution terms; the archive listing contains a `license-cc-by.txt` member, which you should read yourself. Upstream source: [OpenMIC-2018 on Zenodo](https://zenodo.org/record/1432913).

In the first full run the held-out macro average precision was about 0.915 for the linear head and 0.919 for the MLP, against 0.550 for a class prior. Small differences across torch versions or CPUs are possible, and a rerun on a clean machine has not been measured. Results and checkpoints go to the ignored `outputs/` folder.

Optional: `experiments/validate_vggish.py` compares extracted embeddings with OpenMIC's stored features. It needs a few OGG files from the archive (`audio/<prefix>/<key>.ogg`) placed by hand in `data/openmic-2018/audio_subset/`.

The browser UI has been checked by hand in Chrome and a headless browser using synthetic WAV files and OpenMIC clips. That is manual checking, not an automated test.

## License

Apache-2.0. See [LICENSE](LICENSE).

Third-party notices for the packaged app are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Third-party assets: the Geist fonts are under the SIL OFL 1.1 (`web/fonts/OFL.txt`) and the Phosphor icons under MIT (`web/icons/LICENSE`).
