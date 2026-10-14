# Ohrin — Vision and first-version baseline

Project name chosen by Adam: **Ohrin**, derived from German “Ohren” (ears).
Recorded: 2026-10-11. Stage: discovery, before implementation.

## Overall ambition

Ohrin is an interactive music-analysis and creative-reference workbench. A listener should be able to bring a song, understand its musical construction and character, and turn that understanding into a playable interpretation or original creative direction.

The broader ambition includes:
- Key, tempo, beats, harmony, and notes active over time, including changes between passages.
- Genre and subgenre candidates, with room for mixed styles and disagreement.
- Instruments, their roles and activity, and possible performance details such as guitar tuning.
- Arrangement, texture, dynamics, energy, and how sections create or release tension.
- Overall vibe and suitability for contexts, from a hype boxing walkout to a funeral or wake. These are subjective and culturally dependent interpretations, not objective labels.
- Help recreating a song or its overall feeling, and actionable exercises to help artists through creative block.

The UI is a core product requirement: people must be able to use and explore the analysis, not merely view a notebook or static report. Ideally, observations link to audible passages and allow user corrections.

Primary initial audiences, explicitly chosen by Adam:
1. Guitarists learning by ear.
2. Producers studying arrangements.

Proposed organising modes (not yet a final UI design):
- **Recreate:** understand and play a plausible interpretation of a reference.
- **Reimagine:** use selected characteristics to develop original musical ideas.

This is a new Python/ML portfolio project, not an extension of an existing app. It should be impressive to interact with and demonstrate meaningful model training and evaluation. Existing models can support the product, but an API wrapper alone would not fulfil the intended ML learning goal.

## First-version baseline — accomplish this first

Preserve the larger vision above while beginning with a bounded **“break down this passage”** workflow. This is the planning baseline distilled from the conversation, not a claim that every feature has been technically validated or that a final implementation design was approved.

1. **Usable passage workflow:** upload a track, play it, select and loop a passage, and inspect analysis aligned with playback in a browser UI.
2. **Core musical analysis:** estimated tempo and key/chords for the selected passage, with uncertainty and limitations visible.
3. **Guitarist value:** a simplified riff/melody or chord interpretation that can be tried on guitar; slowed playback and possible fingerings are candidate aids. Exact outputs depend on the feasibility investigation.
4. **Producer value:** an account of likely instrument roles and how the passage's arrangement or energy develops, tied to timestamps where possible.
5. **Creative value:** a small number of actionable, evidence-linked exercises for recreating or changing the passage's feeling. Keep suggestions distinct from measured findings.
6. **ML evidence:** select one bounded task for original training/evaluation, compare a simple baseline with the chosen model, document held-out performance, latency, and failure cases. The reference song is a qualitative case study, not a benchmark or training dataset by default.

Deliver the shared passage workflow and a credible, evaluated analysis slice before broadening to whole-song detail. The guitarist and producer perspectives both remain requirements; the exact depth of each is unresolved.

Do not make exhaustive full-mix note transcription, exact tuning recovery, detailed subgenre recognition, source separation, or comprehensive occasion recommendations prerequisites for this first version. They remain longer-term candidates to investigate, not promised capabilities.

## First reference and immediate milestone

- Artist: Deftones.
- Track: “i think about you all the time”.
- Focus: **02:44 (164 seconds) through the end** of the full recording.
- Input location: `audio/reference/`.
- At handoff: folder exists and is empty; audio has not been received, listened to, or analysed.

After audio arrives, inspect the actual file and passage, identify useful outputs for both audiences, and establish what can be measured reliably. Then propose a concrete first-version design and bounded ML task. Do not invent song-specific observations from its title or artist.

## Technical honesty

- Notes in a dense mix and exact instrument identities can be difficult to recover.
- The same pitches can be played with different guitar tunings and fingerings. A suggested tab is one playable interpretation, not proof of the original performance.
- Key and tempo can be ambiguous or vary across sections.
- Mood, genre, and occasion suitability can have multiple valid interpretations.
- Confidence must be evaluated rather than assumed to equal correctness.
- Keep measured outputs, inferred explanations, and creative suggestions distinguishable.

## Unresolved decisions

Dataset and usage rights; training task and metrics; compute and cost budget; model architectures; use of pretrained components; UI design and stack; hosting; audio retention; implementation timeline. Nothing has been installed or implemented and no stack has been chosen.

## Earlier candidate preserved

The environmental Audio Scene Explorer remains a separate potential project at `../project-ideas/audio-scene-explorer.md` relative to the Ohrin project root. It was not silently converted into Ohrin; its environmental classes and top-three predictions are not Ohrin requirements.
