# Reference case study: 02:44 through the end

Inspected: 2026-10-12. Source: `audio/reference/deftones - i think about you all the time [visualizer].mp3` (SHA-256 `7a828d9976780985aebc167d917e5e07067d35aadd5e5d77515c66745bac6616`). The file was decoded locally with FFmpeg; no audio was uploaded. The recording ends at about 04:09.01 in decoded samples, so the analysed interval is 164.00–249.01 s (85.01 s). The earlier incomplete MP3 was replaced by the user; its inspection is not preserved.

## What was actually measured

`experiments/reference_probe.py` decodes the stereo mix at 22,050 Hz, averages channels to mono, and computes 5-second RMS level, spectral flux, broad-band spectral power, and a crude pitch-class peak histogram. Its machine-readable output is `outputs/reference_probe.json` (excluded from Git). A separate one-second RMS check used the same decoder and downmix around the ending. These are signal measurements of a mixed recording, not listening observations or source-separated tracks.

| Interval | Measured observation | Careful interpretation |
| --- | --- | --- |
| 02:44–02:49 (164–169 s) | 5-second RMS about −10.6 dBFS; 40–200 Hz held 64% of measured 40–8,000 Hz spectral power. | A comparatively lower-level, bass-weighted start to the selected passage. The source of that low-frequency energy is unknown. |
| 02:49–03:14 (169–194 s) | RMS about −8.9 to −8.4 dBFS. The 200–2,000 Hz power share rises from 39% to around 43–50%. | Level and frequency balance develop gradually; no instrument entrance can be certified from this alone. |
| 03:14–03:54 (194–234 s) | RMS about −8.4 to −7.6 dBFS for most 5-second windows, with persistent onset flux. Spectral peak pitch-class rankings differ from the preceding span. | The mix remains comparatively strong and rhythmic while harmonic content appears to change. This does not establish a chord sequence. |
| Around 03:53–03:56 (233–236 s) | One-second RMS falls from about −7.7 dBFS at 232 s to −10.9, −13.8, −16.0, then −18.8 dBFS at 233–236 s. | A clear transition into a quieter ending. Whether this is a fade, a change of sources, or both needs listening or stronger analysis. |
| 03:59–04:09 (239–249 s) | 5-second RMS drops from −19.4 dBFS (239–244 s) to −24.5 dBFS (244–249 s); power is concentrated in the 200–2,000 Hz band, and the high-band share is very small. | The ending continues to decay and becomes less bright by these measurements. |

An onset-envelope autocorrelation probe returned candidate periodicities near **136 BPM** and **68 BPM**, with other nearby peaks (including 143.6 BPM). This is a half/double-time ambiguity plus possible rhythmic subdivision, not a validated tempo or beat grid. The crude pitch-class peak method includes harmonics from distorted/overlapping sources; it does **not** justify naming a key, chord progression, riff, tuning, or fingering.

## What remains uncertain

No direct listening was available in this environment. Instrument identities, vocal activity, guitar roles, arrangement causes, exact beat positions, harmony, and playable voicings have not been verified. The supplied mix is one qualitative reference; it cannot measure model generalisation. A future listening review or source-aware method plus user correction should test the proposed interpretations. Do not distribute the recording as a demo or train on it without separate authorisation.

## Scope implications

The strong late transition makes a timestamped energy/texture view useful for producers. The uncertain pulse and pitch-class evidence makes looping, slowed playback, alternate tempo/chord candidates, and user correction important for guitarists. Instrument-presence probabilities could add value, but this analysis does not validate them; the proposed OpenMIC classifier still needs independent held-out evaluation and qualitative checking on this passage. Creative exercises can use the measured level/texture contrast while remaining clearly marked as suggestions.
