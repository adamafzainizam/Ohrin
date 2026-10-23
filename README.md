# Ohrin

[![CI](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml/badge.svg)](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml)

Ohrin takes its name from the German "Ohren", meaning "ears".

It is a local, open-source Python/ML tool with a browser UI for guitarists learning by ear and producers studying how a track is arranged. You pick a passage, loop and slow it, see what changes in level and texture, and get cautious candidates for pulse, chords and instruments that you can correct and export as a private note.

Status: local prototype. The workbench, signal analysis, note export (schema v3), a seven-band listening EQ, a four-class instrument model trained on OpenMIC, a per-block instrument timeline and an in-session listening review all work. The model has held-out results on OpenMIC clips only. Its behaviour on full commercial mixes has not been measured, so the app shows its scores as uncertain model output.

Ohrin is meant to be installed and run on your own machine, not hosted. A packaged desktop app is planned but not built yet.

## Start here

[docs/concept.md](docs/concept.md) holds the longer vision and the first-version baseline. [docs/model-card.md](docs/model-card.md) records the model, its evaluation and its limits.

## Next steps

- Check how the instrument heads transfer to full mixes, using audio you are allowed to label and share.
- Package the app so it can be installed without a Python setup.
- Improve pulse and chord candidates, which are the weakest measured parts.

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
python3 -m pip install 'torch>=2.2' --index-url https://download.pytorch.org/whl/cpu   # CPU index only; a plain install pulls the much larger CUDA build on Linux
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
