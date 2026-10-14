# Ohrin

Derived from the German word “Ohren”, meaning “ears”.

Status: local first-slice implementation in progress. The browser workflow, signal analysis, note export and a four-class OpenMIC instrument-candidate model are implemented. The model has real held-out OpenMIC results; its behaviour on full commercial mixes is not validated.

An open-source Python/ML project with a browser UI for guitarists learning by ear and producers studying reference tracks. Help users understand a passage, recreate a playable interpretation, and develop original ideas inspired by its musical character.

## Start here

Read [the vision and first-version baseline](docs/concept.md). The broader ambition includes musical structure, instruments and performance, genre, mood, occasion suitability, and practical help recreating or reimagining music; the first milestone is a bounded passage-analysis workflow for guitarists and producers.

## Next step

Verify JSON note export, then validate a compatible VGGish extractor so the trained OpenMIC head can be tested on uploaded audio. The official OpenMIC files and saved checkpoints are present locally; [the model card](docs/model-card.md) records the real held-out evaluation. The complete Deftones reference MP3 and its local 02:44–end [signal case study](docs/reference-case-study.md) remain a private qualitative reference.

## Workspace

- `audio/reference/`: local reference recordings supplied by Adam.
- `docs/concept.md`: broader vision, first-version baseline, and unresolved decisions.
- `docs/first-version-scope.md`: evidence from the supplied file and a provisional product/ML scope.
- `docs/reference-case-study.md`: measured findings and limitations for the selected passage.
- `experiments/reference_probe.py`: reproducible local signal probe; generated JSON stays in `outputs/`.
- `experiments/`: exploratory analysis and model training scripts.
- `outputs/`: generated analysis and visualisations.

Audio and generated outputs are excluded from Git by default. Supplying a reference track for local analysis does not make it a public demo asset or an authorised training dataset.

## Run locally

Requires Python 3.12, NumPy (`requirements.txt`), FFmpeg, and a modern browser. Training additionally requires PyTorch (`requirements-ml.txt`). From this folder:

```bash
python3 -m ohrin.server --port 8765
```

Open `http://127.0.0.1:8765`. Choose an audio file under 50 MB, select a passage of up to three minutes by dragging the waveform or entering times, then loop, slow, and analyse it. Uploaded bytes go to a temporary file for FFmpeg and are deleted after analysis. The browser plays a local object URL. Notes export as JSON without audio.

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/app.test.mjs
```

The sandbox blocks localhost sockets, but the server was later run outside it and the UI exercised in Chrome with a synthetic WAV and the private reference MP3. File load, time entry, waveform dragging, playback, looping, analysis rendering, and corrections were checked. The saved JSON export file still needs runtime verification.

## ML training

The [four-class OpenMIC task](docs/model-card.md) has a training and evaluation script. The verified official archive contains `openmic-2018.npz`, `class-map.json`, `partitions/split01_train.csv`, and `partitions/split01_test.csv`; these are extracted under ignored `data/openmic-2018/`. The companion README lists different partition filenames, so follow the actual archive contents. To reproduce the run:

```bash
python3 -m experiments.train_openmic --data-root data/openmic-2018
```

Results and checkpoints are written to ignored `outputs/`. The current held-out macro average precision is 0.550 for class prior, 0.915 for linear, and 0.919 for MLP. Precomputed VGGish features are used for training; the app can show instrument candidates for uploaded passages when the ignored local VGGish weights (`data/vggish/`) and a saved head (`outputs/openmic-mlp.pt`) are present; these are uncertain model outputs, not transcriptions. No hosting provider or public deployment has been selected.

## License

Apache-2.0. See [LICENSE](LICENSE).
