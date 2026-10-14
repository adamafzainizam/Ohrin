# Contributing to Ohrin

Thanks for helping. Ohrin is a small local-first prototype, so please keep changes focused.

## Setup

```bash
python3 -m pip install -r requirements.txt        # core
python3 -m pip install -r requirements-ml.txt     # optional: PyTorch for instrument candidates
```

FFmpeg and Node 22 or newer are also needed.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
node tests/app.test.mjs
```

- Tests must use synthetic signals. They must not read `audio/`, `data/` or `outputs/`, and must not use the network.
- Logic in `web/state.mjs` needs a test in `tests/app.test.mjs`.
- PyTorch-only tests are skipped when PyTorch is missing; keep core imports torch-free.

## What must not be committed

Audio, model weights, checkpoints, datasets (including OpenMIC) and generated `outputs/`. Private recordings are never public demo assets. Do not upload audio to third-party services.

## Honesty rules

- Keep three things apart in code and copy: what was measured from the signal, what a model output (with its limits), and what is a suggestion.
- Never imply audio was heard or analysed unless it was. Do not write "detected", "confirmed", "correct" or "accuracy" for model output or for a user's listening marks.
- Suggested tunings and fingerings are not necessarily the original performance.

## Pull requests

Small, single-purpose PRs. Describe what you ran and what you could not verify. Contributions are accepted under the Apache-2.0 licence of this repository.
