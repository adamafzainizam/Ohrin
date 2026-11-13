# Workbench UI checklist

Browser checks for the workbench revamp. Run the rows tagged with the current slice and every earlier slice after each slice lands. Use only the generated tone and the synthetic fixture. Never load anything from `audio/`, `data/` or `outputs/`.

## Setup

1. Start the server from the worktree on a free port: `~/.venvs/ohrin-ml/bin/python -m ohrin.server --port 8791`.
2. Write the tone outside the repo: `python3 tests/browser/make-tone.py /tmp/claude-1000/ohrin-tone.wav` (240 s, 44.1 kHz mono). Do not commit it. Copy it to a second name when a test needs a different file.
3. Open a named session: `playwright-cli -s=ohrin-revamp open http://127.0.0.1:8791/`.
4. Route the analysis to the fixture: `playwright-cli -s=ohrin-revamp route "**/api/analyze*" --body="$(cat tests/fixtures/synthetic-analysis.json)" --content-type=application/json`. The fixture's passage is 120 s to 158.4 s, with four 9.6 s instrument blocks.
5. Load the tone with `setInputFiles('#audioFile', ...)` through `run-code`, set End to 158.4 and then Start to 120 (End first, see baseline note 1), then analyse.
6. Viewports: 390, 1024 and 1280 wide. The default 720 px height hides the waveform below the fold, so use 900 or more when dragging on the waveform.
7. Finish with `playwright-cli -s=ohrin-revamp close` and stop the server.

Honesty rule: record exactly what was done for each row. A row that could not be automated is marked "not checked" or checked by the closest observable proxy, and the proxy is named.

## Items

| # | Item | From | How to check |
|---|------|------|--------------|
| 1 | Keyboard only: set End then Start, tab to Analyse, press Enter, results appear | S0 | Focus `#endTime` and `#startTime` with the keyboard, type values, Tab out. Focus `#analyzeButton`, press Enter. Wait for `#results` to be visible. |
| 2 | Keyboard only: Tab reaches the block strip as one stop, arrows and Home move between blocks | S0 | From `#analyzeButton` press Tab until the focused `data-focus-id` starts with `seg-`. Press ArrowRight twice, then Home, and read the focused id each time. |
| 3 | Keyboard only: Enter on a block selects it and reveals the block review | S0 | With a block focused press Enter. `#segmentReview h4` names that block and its time range. |
| 4 | Keyboard only: Tab from the strip reaches "Play this block", then the marking radios and Clear buttons | S0 | Press Tab repeatedly from the strip and list the focused ids. |
| 5 | Keyboard only: a mark can be set and cleared with Space and Enter | S0 | Focus a radio, press Space, read `checked`. Focus its Clear button, press Enter, read the tally. |
| 6 | Keyboard only: export | S0 | Focus `#exportButton`, press Enter, capture the download, parse it as JSON. Schema is `ohrin.passage-note` v3. |
| 7 | Focus stays on the control after marking and after clearing | S0 | Read `document.activeElement` data-focus-id straight after Space on a radio and Enter on Clear. It must be unchanged. |
| 8 | Each live-region message is announced once | S0 | Attach a MutationObserver to `#liveStatus` that records every non-empty text. Do one action, wait 250 ms, read the log. One entry per action. Proxy: the observer records text changes, not what a screen reader speaks. |
| 9 | Selecting a block announces it once | S0 | Same observer; press Enter on a block. One "Block N ... selected" entry. |
| 10 | EQ export and re-import give identical settings | S0 | Open the EQ panel, pick a preset, set Trim. Export. Reload, load the tone, analyse, import the note through `#eqImport`, export again. The two `eq` objects are equal. |
| 11 | EQ import announces success once | S0 | Observer on `#liveStatus` during the import. |
| 12 | Abandoned drag restores the selection and keeps the results | S0 | Press on the waveform, move past the threshold, dispatch `pointercancel`. Selection label returns to the earlier value and `#results` stays visible. |
| 13 | A move with no button held abandons the drag | S0 | Press, move, dispatch a `pointermove` with `buttons: 0`. Same expectation as row 12. |
| 14 | A committed drag changes the selection and clears the results | S0 | Press, move, release. Selection label changes and `#results` is hidden. |
| 15 | A stale analysis response is ignored when the selection changes in flight | S0 | Route `/api/analyze` with a 2.5 s delay. Press Analyse, change End before the response arrives, wait. Results stay hidden and the button is re-enabled. |
| 16 | A stale analysis response is ignored when another file loads in flight | S0 | Same delayed route. Press Analyse, load a second file (a copy with another name), wait. Results stay hidden, new file name shown, button enabled. |
| 17 | No horizontal overflow at 390, 1024 and 1280, EQ closed and open, results showing | S0 | For each width: `document.documentElement.scrollWidth` equals `clientWidth`. Today's UI has one theme only. |
| 18 | No horizontal overflow in both themes at 390, 1024 and 1280 | S1 | As row 17 with the theme set to Light and Dark. |
| 19 | Theme: System, Light, Dark work; no flash of the wrong theme on reload; canvases redraw on change | S1 | Switch each option, reload with the stored choice, take screenshots in both themes and inspect them. |
| 20 | Fixture passage: energy, blocks and playhead line up with the waveform at three zoom levels | S2 | Analyse with the fixture (passage starts at 120 s). At three zoom levels compare the x positions of the energy line, the block edges and the playhead with the waveform for the same track time. |
| 21 | Zoom and pan never change the selection | S2 | Read Start and End before and after zooming and panning. |
| 22 | `+` and `-` typed into Start, End, notes and EQ fields do not zoom | S2 | Type the characters into each field and confirm the zoom level and the field text. |
| 23 | Zoom announcements are debounced | S2 | Zoom repeatedly in under a second and count entries in the `#liveStatus` log. |
| 24 | Stale banner after a committed new selection; notes and marks kept | S3 | Analyse, mark a block, type notes, commit a new selection. Banner shows, notes and marks remain. |
| 25 | "Play this block" plays the block | S3 | Click it and read `currentTime` and the playback bounds. |
| 26 | Export records the analysed passage, not the current selection | S3 | Change the selection after analysing, export, read `passage`. |
| 27 | Re-analysis announces what it cleared | S3 | Re-analyse after marking and read the `#liveStatus` log. |
| 28 | Window drop loads audio; dropping JSON on the EQ import still imports EQ | S4 | Dispatch drop events carrying a WAV on the window and a note JSON on the EQ import. |
| 29 | Keyboard path through inspector tabs and the block strip | S5 | Tab and arrow through inspector tabs, then the strip, mark a block, export. |
| 30 | EQ model matches `BiquadFilterNode.getFrequencyResponse` within 0.1 dB; export and import round trip | S6 | Compare the model curve to the node response at the plotted frequencies for several presets. |
| 31 | Screenshots in both themes inspected | S7 | Capture at 390 and 1280 in Light and Dark and look at each image. |

## Baseline: S0 rows against the unchanged UI

Run on 2026-11-13 in Chromium through playwright-cli, session `ohrin-revamp`, server on port 8791, the generated tone and the synthetic fixture only. Branch `feat/workbench-revamp` before any revamp change to `web/`.

| # | Result | What was done |
|---|--------|---------------|
| 1 | Pass | Typed End 158.4 then Start 120 with the keyboard, Tab to `#analyzeButton` (via `loop` and `eqDisclosure`), Enter. Results shown: 2 observations, chords Em, C, Am, 4 blocks. |
| 2 | Pass | Tab from Analyse reached `seg-0` in one press. ArrowRight went to `seg-1`, `seg-2`; Home returned to `seg-0`. |
| 3 | Pass | Enter on `seg-0`: heading "Segment 02:00-02:09 (about 9.6 s block)". |
| 4 | Pass | Tab order from the strip: `seg-play`, `review-seg-0-guitar-heard`, `review-seg-0-guitar-clear`, `review-seg-0-bass-heard`, `review-seg-0-bass-clear`, `review-seg-0-drums-heard`. |
| 5 | Pass | Space on the heard radio gave `checked: true`. Enter on Clear set the tally back to 0 marks. |
| 6 | Pass | Enter on Export downloaded `ohrin-passage-note.json`, `ohrin.passage-note` schema_version 3, passage 120 to 158.4, one block mark recorded. |
| 7 | Pass | Focus id unchanged after Space on the radio and after Enter on Clear. |
| 8 | Pass (proxy) | MutationObserver on `#liveStatus`: one entry after marking ("You marked 0 of 4 candidates. 1 block mark set.") and one after clearing. Screen reader output not checked. |
| 9 | Pass (proxy) | `#liveStatus` read after Enter on a block: "Block 1, 02:00 to 02:09 selected. Marking controls are below the timeline." Read once after the action; no observer was attached yet, so exactly-once is not proven for this row. |
| 10 | Pass | Preset `low_focus` and Trim -2.5 exported, reload, import, export: the two `eq` objects were equal. |
| 11 | Pass (proxy) | `#liveStatus` read "EQ settings loaded." after the import. Read once after the action; not counted with an observer. |
| 12 | Pass | During the drag the label read 00:22-01:07; after `pointercancel` it returned to 00:00-00:30 and results stayed visible. |
| 13 | Pass | Same, with a `pointermove` carrying `buttons: 0`. |
| 14 | Pass | A normal drag changed the selection to 00:22-01:07 and hid the results. |
| 15 | Pass | With a 2.5 s delayed route: Analyse showed "Analysing...", End changed to 40, after the response results stayed hidden and the button was enabled. |
| 16 | Pass | Loaded `ohrin-tone-b.wav` while in flight; after the response results stayed hidden, file name was the new one, button enabled. |
| 17 | Pass | `scrollWidth` equalled `clientWidth` at 390, 1024 and 1280, with the EQ panel closed and open, results showing. One theme only (dark); no `prefers-color-scheme` rule exists in `web/style.css`. |

Rows 18 to 31 start in later slices and were not run.

### Baseline notes

1. Typing Start before End when End is still smaller than Start swaps them. With the default End of 30, entering Start 120 gave 30 to 120, not 120 to 30. Setting End first avoids it. This is today's behaviour (`clampSelection` orders the pair) and was not changed.
2. The first attempt at row 16 re-selected the same file; no `change` event fired and nothing was loaded. A copy with another name was needed. Not an app issue.
3. At the default 720 px height the waveform sits below the fold. Drag checks need a taller viewport.
4. The only console error was `favicon.ico` 404.
5. Not checked: touch, screen readers, Firefox, Safari, WebKitGTK, whether audio is audible, EQ audio output.
