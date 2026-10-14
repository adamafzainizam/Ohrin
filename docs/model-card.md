# Ohrin instrument-presence model card

Status: **trained and evaluated on the official OpenMIC-2018 split on 2026-10-12**. The saved heads are still not usable on arbitrary uploaded audio because Ohrin does not have compatible VGGish extraction. Do not present instrument probabilities in the UI yet.

## Intended task

Predict guitar, bass, drums, and voice presence in a **10-second mixed-music clip**. These are coarse clip-level tags, not isolated stems, performance roles, or exact entrance/exit times. The other Ohrin views currently use direct signal measurements and experimental pulse/harmonic candidates.

## Training data and feature boundary

The dataset is [OpenMIC-2018](https://zenodo.org/records/1432913). Its [official companion notebook](https://github.com/cosmir/openmic-2018/blob/master/examples/modeling-baseline.ipynb) documents 20,000 ten-second clips, 10×128 VGGish embeddings per clip, partially observed `Y_true`/`Y_mask` labels, and the official split. The pipeline averages the ten embeddings, divides their 0–255 values by 255, and thresholds observed label probabilities at 0.5. Missing labels never become negatives. It preserves the official test split; validation is grouped by source track within training. The official notebook says the test split was constructed to avoid artist overlap.

The downloaded `openmic-2018-v1.0.0.tgz` matched Zenodo's published MD5, `e4ccf187e2bb5ab2e115416e8aafe7f4`. The extracted NPZ has SHA-256 `3e5f9252e2f983737d13f87a52b3ee02c8cad357f00531762490b31f18d3e62e`. Its sample keys are stored as a NumPy object array, so the loader checks that exact NPZ hash before allowing pickle for that field. The actual archive uses `partitions/split01_train.csv` and `partitions/split01_test.csv`, despite different filenames in the companion README.

The trained linear and MLP heads use **precomputed pretrained features**. This is supervised training, but it is not end-to-end audio model training. The current local passage analyser has no VGGish extractor, so these heads cannot yet produce valid predictions for arbitrary user audio or the Deftones reference. An inference path requires the same VGGish preprocessing and weights, plus a qualitative transfer check. This limitation is intentional in the UI: no untrained or incompatible instrument probabilities are shown.

## Evaluation protocol

`experiments/train_openmic.py` compares class-prior, linear, and MLP heads. It reports per-class average precision, precision/recall/F1 at thresholds selected on validation, Brier score, observed support, macro average precision, CPU head latency, and the strongest observed false positives/negatives by sample key. Saved checkpoints and metrics go to `outputs/` and are excluded from Git. Do not use the Deftones passage as the test set.

## Held-out results

The official split has 14,915 training-side clips and 5,085 held-out test clips. Source-track-grouped validation divided the training side into 11,932 training and 2,983 validation clips; source-track IDs have zero overlap across these three sets. Only observed labels count in metrics. The test set has 436 observed guitar labels (286 positive), 463 bass (134 positive), 424 drums (278 positive), and 374 voice (224 positive). This is sparse coverage of the 5,085 clips, not a fully labeled four-class test set.

| Model | Macro average precision | Guitar AP | Bass AP | Drums AP | Voice AP |
| --- | ---: | ---: | ---: | ---: | ---: |
| Class prior | 0.550 | 0.656 | 0.289 | 0.656 | 0.599 |
| Linear | 0.915 | 0.991 | 0.726 | 0.976 | 0.967 |
| MLP | 0.919 | 0.995 | 0.722 | 0.981 | 0.978 |

At thresholds chosen on validation, held-out F1 is 0.956/0.660/0.936/0.934 for the linear model and 0.969/0.630/0.938/0.935 for the MLP (guitar/bass/drums/voice). Bass is the clearest weakness: the MLP's bass precision is 0.531 and recall 0.776. The MLP's Brier scores are 0.035/0.137/0.064/0.067; these describe observed-label error, not a guarantee that its scores are calibrated on user songs.

On this CPU, training took about 0.65 s for the linear head and 0.99 s for the MLP, each reaching the configured 80-epoch cap. The measured inference was about 0.004 ms and 0.017 ms per precomputed 10-second embedding, respectively. These timings exclude audio decoding and VGGish feature extraction, so they are not end-to-end app latency. The MLP's macro AP advantage over linear is only 0.004; no deployment model is selected from that difference alone.

The saved error keys show concrete cases for review. Both models strongly score the observed bass-negative clip `086680_42240` as positive and the observed bass-positive clip `041769_80640` as negative. Guitar false-positive `047363_15360` and voice false-negative `152200_172800` also recur. These are label-versus-score disagreements; the clips have not been listened to, so their musical causes are unknown. Full per-class precision, recall, F1, Brier scores, thresholds, error keys, and checkpoints are in ignored `outputs/openmic-metrics.json` and `outputs/openmic-{linear,mlp}.pt`.

## Current limits and next gate

The held-out evaluation establishes a real supervised classification result on supplied VGGish embeddings. It does not establish transfer to arbitrary uploaded songs or the Deftones reference, detect exact instrument entry times, or identify performance roles. Add and validate matching VGGish preprocessing and weights before using a saved head in the UI; then check qualitative transfer on permitted audio. The public dataset's attribution/license terms and each audio example's metadata must be respected if examples are ever distributed.

## Inference on uploaded audio (2026-10-14)

`ohrin/vggish.py` reproduces the AudioSet VGGish front end and uses the torchvggish port weights plus Google's PCA parameters (not distributed in this repository; place them in `data/vggish/`). On 8 official OpenMIC OGG clips, extracted embeddings had a mean absolute difference of 2-6 (0-255 scale) from the stored features on 7 clips and 22.9 on one quiet clip; head probabilities changed by at most 0.07, with one threshold flip in 16 clip-head pairs. This is a small compatibility check, not a re-evaluation.

Measured on a CPU-only laptop (6 threads): about 0.35 s warm for a 10 s clip including upload handling, 0.85 s on the first call (model load), and about 1.3 s for an 85 s passage. Segments shorter than 10 s are out of distribution for the heads (trained on 10 s means). Performance on full commercial mixes, as opposed to 10 s OpenMIC clips, has not been measured.
