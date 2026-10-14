# First-version scope proposal

Status: this records the original bounded recommendation. The local prototype and the OpenMIC training and evaluation have since been completed (see the [model card](model-card.md)). It is not an approved final architecture.

## Reference evidence

Local signal measurements on one private recording showed that a timestamped level and texture view is useful: a sharp level decline was visible, and pulse candidates were ambiguous (a half-time and a double-time reading). No key, chord, instrument or guitar part was verified. That recording is not distributed, and the measurements are not a benchmark.

## Proposed product slice

One local-first browser workflow accepts a user's track, shows a waveform and playback cursor, and lets them select, loop, and slow a passage. Analysis panels link every claim to the selected timestamps and identify whether it is a measurement, a model inference, a user correction, or a creative suggestion.

- Guitarist: tempo/beat and key/chord candidates, with ambiguity shown; a simple playable chord interpretation with suggested voicings and slowed looping. Voicings are possibilities, not recovered original fingering or tuning. A full-mix riff/tab transcription is outside this first slice.
- Producer: measured loudness/onset/texture changes across the selected passage; instrument-presence probabilities in coarse windows for guitar, bass, drums, and voice; short timestamped arrangement observations that distinguish measured changes from uncertain role descriptions. Presence is not source separation or exact entry/exit timing.
- Creative use: two or three editable exercises tied to observations, such as retaining a chord rhythm while changing density or shifting an instrument's entrance. These are suggestions, not detections.
- Usability: corrections for beat/chord candidates and instrument claims; export a compact private passage note. Uploaded recordings remain local by default. No private recording is a public demo asset or a training example.

Use a small, inspectable MIR pipeline for beat, key/chord candidates, and signal features. The case study validates the usefulness of a timestamped dynamics/texture view, but it has not validated chord or instrument algorithms. Select concrete algorithms after listening review and compute constraints are checked. Confidence shown in the UI must reflect actual validation or clearly be labelled a candidate score.

## Bounded original ML task

Task: predict the presence or absence of guitar, bass, drums, and voice in a 10-second mixed-music window. This directly supports the producer activity view and helps guitarists locate likely guitar-bearing windows. It does not predict performance roles or note content.

Candidate data: [OpenMIC-2018](https://zenodo.org/records/1432913) supplies 20,000 ten-second clips, annotations for 20 instruments, VGGish features, audio, and train/test partitions. Its [paper](https://brianmcfee.net/papers/ismir2018_openmic.pdf) describes partially observed labels and confirms the four target classes. Download size is 2.6 GB for the archive; check storage, compute, licensing, and any artist/track overlap before committing to training. Do not treat missing labels as negatives. OpenMIC's CC BY 4.0 terms and per-recording metadata need review before any public distribution of examples.

Training and comparison: start with supplied VGGish embeddings to keep compute bounded. Fit a regularised linear classifier per class as the baseline, then train a small multilabel MLP with masked loss on observed labels only. Freeze the feature extractor; describe this accurately as training a supervised classifier on pretrained embeddings. Use the supplied test split untouched, carve validation from training, and inspect artist/track overlap; use grouped splitting if the metadata supports it. A later log-mel CNN experiment is optional if compute and evidence justify it.

Evaluation: report per-class average precision and precision/recall/F1 at validation-selected thresholds, macro average precision, observed-label counts, CPU inference time per 10-second window, calibration/reliability, and failure cases (especially dense distorted mixes and instrument confusions). Compare against both class-prior and linear baselines. Treat any private recording as a qualitative case study only, and add model outputs to it only after training and listening checks; it is not a held-out benchmark.

Honesty boundary: OpenMIC's 10-second clip labels can support coarse instrument-presence probabilities. Overlapping windows may make a useful display but do not turn the model into a precise activity detector. A few manually checked passage windows are needed before writing song-specific arrangement observations.

## Concrete deliverables and acceptance checks

1. A reproducible inspection note for a recording, with file provenance, valid decoded duration, passage timestamps, measured data and separately labelled interpretations.
2. A browser prototype that can load a local track, select/loop/slow a passage, keep analysis synchronized, accept corrections, and export a private note.
3. An inspectable analysis pipeline for beat/tempo, key/chord candidates, loudness/onset/texture changes, and simplified chord voicings; every output includes timestamps and limitations.
4. A training/evaluation package for the four-class instrument task, with data preparation, masked-label handling, fixed splits, baseline and MLP training, saved metrics, latency, and error examples.
5. A short model card and UI walkthrough showing where each audience gets value, and what remains uncertain.

Before selecting the final stack or training run, confirm available compute/storage and intended deployment/public access. The dataset and model choice remain a recommendation until those constraints and a listening review are checked.
