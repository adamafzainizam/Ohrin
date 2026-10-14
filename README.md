# Ohrin

[![CI](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml/badge.svg)](https://github.com/adamafzainizam/Ohrin/actions/workflows/ci.yml)

Derived from the German word “Ohren”, meaning “ears”.

Status: local prototype. The browser workflow, signal analysis, note export (schema v2), a four-class OpenMIC instrument-candidate model, a per-block instrument timeline and an in-session listening review are implemented. The model has real held-out results on OpenMIC clips; its behaviour on full commercial mixes is not validated, and the app presents its scores as uncertain model output.

An open-source Python/ML project with a browser UI for guitarists learning by ear and producers studying reference tracks. Help users understand a passage, recreate a playable interpretation, and develop original ideas inspired by its musical character.

## Start here

Read [the vision and first-version baseline](docs/concept.md). The broader ambition includes musical structure, instruments and performance, genre, mood, occasion suitability, and practical help recreating or reimagining music; the first milestone is a bounded passage-analysis workflow for guitarists and producers.

## Next step

Check how the instrument heads transfer to full mixes on permitted audio, and decide whether a larger or better-suited model is needed. [The model card](docs/model-card.md) records the held-out OpenMIC evaluation and its limits. The Deftones reference recording itself is private and not distributed. The linked [case study](docs/reference-case-study.md) contains only local signal measurements and limitations; no model scores on it are published.

<!-- Screenshot: docs/img/workbench.png (to add; use synthetic or permitted audio only, never the private reference) -->

## Workspace

- `audio/reference/`: local reference recordings supplied by Adam.
- `docs/concept.md`: broader vision, first-version baseline, and unresolved decisions.
- `docs/first-version-scope.md`: evidence from the supplied file and a provisional product/ML scope.
- `docs/reference-case-study.md`: measured findings and limitations for the selected passage.
- `experiments/reference_probe.py`: reproducible local signal probe; generated JSON stays in `outputs/`.
- `experiments/`: exploratory analysis and model training scripts.
- `outputs/`: generated analysis and visualisations.

Audio and generated outputs are excluded from Git by default. Supplying a reference track for local analysis does not make it a public demo asset or an authorised training dataset.

## Requirements

- Core workbench: Python 3.12, FFmpeg and NumPy (`requirements.txt`), plus a modern browser.
- Instrument candidates additionally need PyTorch (`requirements-ml.txt`), the fetched VGGish weights and a trained head in `outputs/`. Without any of these the app still runs and shows an "instrument model unavailable" state with the reason.

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 -m ohrin.server --port 8765
```

Open `http://127.0.0.1:8765`. Choose an audio file under 50 MB, select a passage of up to three minutes by dragging the waveform or entering times, then loop, slow, and analyse it. Uploaded bytes go to a temporary file for FFmpeg and are deleted after analysis. The browser plays a local object URL. Notes export as JSON without audio. Listening marks are kept in memory for the open tab and included only in the exported note.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/app.test.mjs
```

Tests use synthetic signals and need no weights, datasets or audio. PyTorch-dependent tests are skipped when PyTorch is not installed. The workflow in `.github/workflows/ci.yml` runs both suites with and without PyTorch; it has not necessarily been run yet, so check the badge or the Actions tab rather than assuming it is green.

## Fetch weights and reproduce the model

VGGish weights are downloaded from their upstream sources and are not redistributed here: the PyTorch port release from [`harritaylor/torchvggish`](https://github.com/harritaylor/torchvggish) and Google's AudioSet `vggish_pca_params.npz` (see the original [VGGish](https://github.com/tensorflow/models/tree/master/research/audioset/vggish) page in `tensorflow/models`). Check each upstream licence yourself before reuse; this repository does not restate or vouch for their terms. Downloads are size-limited and verified against recorded SHA-256 values.

```bash
python3 scripts/fetch_vggish.py            # -> data/vggish/
python3 -m pip install 'torch>=2.2' --index-url https://download.pytorch.org/whl/cpu   # CPU index only; a plain install pulls the much larger CUDA build on Linux
python3 -m pip install -r requirements-ml.txt   # remaining requirements from PyPI
python3 scripts/fetch_openmic.py --yes     # ~2.6 GB; extracts four files to data/openmic-2018/
python3 -m experiments.train_openmic --data-root data/openmic-2018   # writes outputs/openmic-{linear,mlp}.pt + metrics
python3 -m ohrin.server
```

`fetch_openmic.py` without `--yes` only prints the download size (unless the archive is already cached at `data/openmic-2018-v1.0.0.tgz`, in which case it extracts it). Use `--archive PATH` for an already-downloaded `openmic-2018-v1.0.0.tgz` (MD5 `e4ccf187e2bb5ab2e115416e8aafe7f4`). OpenMIC-2018 is distributed under its own licence and attribution terms; the archive listing contains a `license-cc-by.txt` member (read it yourself; this repository does not restate its terms). Upstream source: [OpenMIC-2018 on Zenodo](https://zenodo.org/record/1432913).

On the 2026-10-12 run the held-out macro average precision was about 0.915 (linear) and 0.919 (MLP), against 0.550 for a class prior. Small differences across torch versions or CPUs are possible; re-running the whole sequence on a clean machine has not been measured. Results and checkpoints are written to ignored `outputs/`.

Optional: `experiments/validate_vggish.py` compares extracted embeddings with OpenMIC's stored features and needs a few OGG files from the archive (`audio/<prefix>/<key>.ogg`) placed manually in `data/openmic-2018/audio_subset/`.

The sandbox used during development blocks localhost sockets; the server was run outside it and the UI exercised in Chrome and a headless browser with synthetic WAV files and, locally, the private reference MP3. That is manual checking, not an automated test.

## License

Apache-2.0. See [LICENSE](LICENSE).
