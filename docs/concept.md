# Ohrin: vision and first-version baseline

Ohrin takes its name from the German "Ohren" (ears). This document keeps the long-term ambition separate from what is built. For current status, see the [README](../README.md) and the [model card](model-card.md).

## Overall ambition

Ohrin is an interactive music-analysis and creative-reference workbench. A listener brings a song, works out how it is built and what character it has, and turns that into a playable interpretation or an original creative direction.

The broader ambition includes:

- Key, tempo, beats, harmony, and notes active over time, including changes between passages.
- Genre and subgenre candidates, with room for mixed styles and disagreement.
- Instruments, their roles and activity, and possible performance details such as guitar tuning.
- Arrangement, texture, dynamics, energy, and how sections build or release tension.
- Overall vibe and suitability for contexts, from a boxing walkout to a funeral. These are subjective, culture-dependent interpretations, not objective labels.
- Help recreating a song or its overall feeling, and practical exercises for getting past creative block.

The UI is a core requirement. People should be able to use and explore the analysis, and ideally observations link to audible passages and accept user corrections.

The first audiences are guitarists learning by ear and producers studying arrangements.

Two organising modes are proposed (not a final UI design):

- Recreate: understand and play a plausible interpretation of a reference.
- Reimagine: use selected characteristics to develop original musical ideas.

Ohrin is meant to be a new Python/ML project with meaningful model training and evaluation. Existing models can support it, but an API wrapper alone would not meet that goal.

## First-version baseline

The first version starts with a bounded "break down this passage" workflow while keeping the larger vision above. This is a planning baseline, not a claim that every feature has been technically validated.

1. Passage workflow: load a track, play it, select and loop a passage, and inspect analysis aligned with playback in a browser UI.
2. Core musical analysis: estimated tempo and key or chords for the selected passage, with uncertainty and limitations visible.
3. Guitarist value: a simplified riff, melody or chord interpretation to try on guitar. Slowed playback and possible fingerings are candidate aids, and exact outputs depend on feasibility.
4. Producer value: likely instrument roles and how the arrangement or energy develops, tied to timestamps where possible.
5. Creative value: a few actionable, evidence-linked exercises for recreating or changing a passage's feeling, kept distinct from measured findings.
6. ML evidence: one bounded task for original training and evaluation, a simple baseline compared with the chosen model, and documented held-out performance, latency and failure cases.

The shared passage workflow and a credible, evaluated analysis slice come before whole-song detail. Both the guitarist and producer perspectives remain requirements, and the depth of each is still open.

Exhaustive full-mix note transcription, exact tuning recovery, detailed subgenre recognition, source separation and comprehensive occasion recommendations are not prerequisites for the first version. They are longer-term candidates to investigate, not promised capabilities.

## Reference recordings

During development the maintainer used one private recording as a qualitative case study. It is not distributed, is not a benchmark, is not training data, and is not part of this repository. A recording you analyse locally stays on your machine.

Look at the actual file and passage before drawing conclusions. Do not infer song-specific observations from a title or an artist name.

## Technical honesty

- Notes in a dense mix and exact instrument identities can be hard to recover.
- The same pitches can be played with different tunings and fingerings. A suggested tab is one playable interpretation, not proof of the original performance.
- Key and tempo can be ambiguous or change between sections.
- Mood, genre and occasion suitability have more than one valid reading.
- A confidence score is not evidence of correctness until it has been evaluated.
- Measured outputs, inferred explanations and creative suggestions should stay distinguishable.

## Open decisions

Dataset and usage rights beyond OpenMIC, further training tasks and metrics, compute and cost budget, whether larger or better-suited models are needed, packaging, audio retention and the implementation timeline.
