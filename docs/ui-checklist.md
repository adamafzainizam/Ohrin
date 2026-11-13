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
| 4 | Keyboard only: Tab from the strip reaches "Play this block", then the marking radios and Clear buttons | S0 | Press Tab from the strip through the active inspector tab and panel to Play, then the marking radios and Clear buttons; list the focused ids. |
| 5 | Keyboard only: a mark can be set and cleared with Space and Enter | S0 | Focus a radio, press Space, read `checked`. Focus its Clear button, press Enter, read the tally. |
| 6 | Keyboard only: export | S0 | Focus `#exportButton`, press Enter, capture the download, parse it as JSON. Schema is `ohrin.passage-note` v3. |
| 7 | Focus stays on the control after marking and after clearing | S0 | Read `document.activeElement` data-focus-id straight after Space on a radio and Enter on Clear. It must be unchanged. |
| 8 | Each live-region message is announced once | S0 | Attach a MutationObserver to `#liveStatus` that records every non-empty text. Do one action, wait 250 ms, read the log. One entry per action. Proxy: the observer records text changes, not what a screen reader speaks. |
| 9 | Selecting a block announces it once | S0 | Same observer; press Enter on a block. One "Block N ... selected" entry. |
| 10 | EQ export and re-import give identical settings | S0 | Open the EQ panel, pick a preset, set Trim. Export. Reload, load the tone, analyse, import the note through `#eqImport`, export again. The two `eq` objects are equal. |
| 11 | EQ import announces success once | S0 | Observer on `#liveStatus` during the import. |
| 12 | Abandoned drag restores the selection and keeps the results | S0 | Press on the waveform, move past the threshold, dispatch `pointercancel`. Selection label returns to the earlier value and `#results` stays visible. |
| 13 | A move with no button held abandons the drag | S0 | Press, move, dispatch a `pointermove` with `buttons: 0`. Same expectation as row 12. |
| 14 | A committed drag changes the selection and retains findings as stale | S7 | Press, move, release. Selection changes, findings remain, and `#staleBanner` shows. Earlier slices cleared findings before S7. |
| 15 | A stale analysis response is ignored when the selection changes in flight | S0 | Route `/api/analyze` with a 2.5 s delay. Press Analyse, change End before the response arrives, wait. Existing findings remain unchanged; without prior analysis, `.inspector-content` stays hidden. The button is re-enabled. |
| 16 | A stale analysis response is ignored when another file loads in flight | S0 | Same delayed route. Press Analyse, load a second file (a copy with another name), wait. Empty inspector states appear, new file name is shown, and the button is enabled. |
| 17 | No horizontal overflow at 390, 1024 and 1280, EQ closed and open, results showing | S0 | For each width: `document.documentElement.scrollWidth` equals `clientWidth`. Today's UI has one theme only. |
| 18 | No horizontal overflow in both themes at 390, 1024 and 1280 | S3 | As row 17 with the theme set to Light and Dark. |
| 19 | Theme: System, Light, Dark work; no flash of the wrong theme on reload; canvases redraw on change | S3 | Switch each option, reload with the stored choice, take screenshots in both themes and inspect them. |
| 20 | Fixture passage: energy, blocks and playhead line up with the waveform at three zoom levels | S5b | Analyse with the fixture (passage starts at 120 s). At three zoom levels compare the x positions of the energy line, the block edges and the playhead with the waveform for the same track time. |
| 21 | Zoom and pan never change the selection | S5b | Read Start and End before and after zooming and panning. |
| 22 | `+` and `-` typed into Start, End, notes and EQ fields do not zoom | S5b | Type the characters into each field and confirm the zoom level and the field text. |
| 23 | Zoom announcements are debounced | S5b | Zoom repeatedly in under a second and count entries in the `#liveStatus` log. |
| 24 | Stale banner after a committed new selection; notes and marks kept | S7 | Analyse, mark a block, type notes, commit a new selection. Banner shows, notes and marks remain. |
| 25 | "Play this block" plays the block | S7 | Click it and read `currentTime` and the playback bounds. |
| 26 | Export records the analysed passage, not the current selection | S7 | Change the selection after analysing, export, read `passage`. |
| 27 | Re-analysis announces what it cleared | S7 | Re-analyse after marking and read the `#liveStatus` log. |
| 28 | Window drop loads audio; dropping JSON on the EQ import still imports EQ | S4 | Dispatch drop events carrying a WAV on the window and a note JSON on the EQ import. |
| 29 | Keyboard path through inspector tabs and the block strip | S6 | Tab and arrow through inspector tabs, then the strip, mark a block, export. |
| 30 | EQ model matches `BiquadFilterNode.getFrequencyResponse` within 0.1 dB; export and import round trip | S7 | Compare the model curve to the node response at the plotted frequencies for several presets. |
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


## Final S7 validation: 2026-11-13, Codex

Tasks 11–14 are implemented and await Claude/Opus review. Chromium via playwright-cli, named session `ohrin-revamp`, worktree server on 8791. Only the generated tone and synthetic fixture (including unavailable-model and shifted-passage variants) were used. No real audio.

| Width | System | Light | Dark |
|---|---|---|---|
| 390 | Pass | Pass | Pass |
| 1024 | Pass | Pass | Pass |
| 1280 | Pass | Pass | Pass |

Each cell covers the common, zoom, inspector-tabs, three-level axis, drop/playhead, and final stale/EQ groups. EQ was open and closed for overflow checks. System was exercised with emulated light preference. All 54 groups pass after the reset fix; the common group was repeated in all nine cells after a first trial exposed that bug.

| Rows | Observed validation |
|---|---|
| 1 | Keyboard End/Start, Tab, Enter Analyse. Additional keyboard Open/Enter opened the native chooser; automated upload delivered the generated WAV. |
| 2–7 | One tab stop in strip; arrows/Home/End; Enter selects; active tab/panel then Play and marking controls in Tab order; Space/Enter mark and clear with focus preserved; actual schema-v3 download. |
| 8–9, 11 | One non-empty live-region mutation per action, observed with MutationObserver. This is a proxy for speech output. |
| 10 | Preset/Trim settings exported, reset/imported in every matrix cell. Additional reload/import/export had exactly equal JSON eq objects, and import did not enable EQ. |
| 12–16 | Cancel/buttons:0 abandons; committed drag keeps stale findings; explicitly gated responses discarded after selection or file change. Selected-block/new-file reset regression fixed and rerun. |
| 17–18 | scrollWidth equals clientWidth, all nine cells, EQ closed/open. |
| 19 | System removes data-theme; Light/Dark apply their setting; stored theme restored by head script on reload; canvas theme changes visible in screenshots. No-flash is a head-script/persistence proxy, not a frame-by-frame measurement. |
| 20–23 | Canvas energy coordinates, DOM block edges and playhead align within 1.5 CSS px at spans 38.4,25.6,17.0667 s; selection unchanged by view changes; actual minus keys in inputs/notes/Trim do not zoom; repeated zoom gives one debounced message. Ctrl-wheel prevented, plain vertical wheel untouched, overview/follow paths passed. |
| 24–27 | Exact stale banner retains notes/marks; block range independent of selection/Loop stops once; export uses analysed passage; reanalysis clears and announces applicable marks/notes. Failed/discarded requests retain prior association; server-adjusted bounds do not falsely mark fresh results stale. |
| 28 | Window drop loads generated WAV; EQ file-input drop not intercepted, audio unchanged, drop class cleared. Native file selection/import exercised separately (drop default action proxy). |
| 29 | All tabs, automatic arrows/Home/End/wrap, persistent note node identity/values/export, exact block message and preserved focus, reduced-motion offscreen scrolling, no-model reason with hidden tally and measured lane retained. |
| 30 | Four presets versus actual BiquadFilterNode.getFrequencyResponse, 512 frequencies each: worst error 0.0000076403 dB (<0.1 dB). EQ reload/import/export JSON equality passed. |
| 31 | Inspected Light/Dark screenshots at all three widths, plus stale full-track view: readable controls/labels, no clipping/overlap, hatch/plain-gap distinction and responsive inspector placement. |

Additional checks: waveform canvas width/height is not reset per playback frame; analysis builds the block strip once (four nodes, not eight); stale opacity affects fill only (0.4), while glyphs/labels/radios/banner stay 1 and Play/Export stay enabled; Not analysed uses plain fill.

Final automated suites at that point: 95 Node tests pass; 252 Python tests pass (superseded by the fix-wave counts below). No known failing application check remains. Unverified: physical touch, screen-reader speech, human listening/audio output, Firefox/Safari/WebKitGTK, Windows/macOS, CPU/memory on long tracks and literal first-paint flash timing. Task 15 final review remains Claude's work.


## Final fix wave validation: 2026-11-13, Claude (Sonnet implementer)

Chromium via playwright-cli, session `ohrin-fix`, worktree server on port 8792, generated 240 s tone (two copies), a generated 6 s tone, a 16-byte non-audio file named `.wav`, and the synthetic fixture only. Scripted with `run-code`; viewport 1280 unless stated. Session and server were closed afterwards.

Automated suites after the wave: 112 Node tests pass (`node tests/app.test.mjs`); 252 Python tests pass (`unittest discover`). These counts were observed by running the suites, not carried over.

| Finding | Check | Result |
|---|---|---|
| F1 | Load tone, analyse with the fixture (4 blocks, 4 table rows, caption), then load a second tone with `decodeAudioData` delayed 1.5 s. During and after the decode: 0 blocks, 0 table rows, empty hidden caption, strip and instrument lanes hidden. | Pass |
| F1, F14 | Same start, then load a non-audio `.wav`: 'The browser could not play this file.', all seven controls and zoom buttons disabled, lanes hidden, empty state shown, readout 00:00.0. Valid file with `decodeAudioData` rejecting: same, with the decode message. Loading a good file afterwards re-enabled everything. | Pass |
| F1 | An old file's decode error not overwriting the new file's status. | Not driven in a browser (needs two overlapping loads); covered only by the `store.get().file === chosen` guard in `loadFile`. |
| F2 | Instruments tab after analysis: `.provenance-label` reads "Model output, uncertain" and has a non-zero box. | Pass |
| F3 | 6 s tone, selection 3 to 6 s (ends at the track end), Loop on, muted playback for 7.5 s: still playing at 4.4 s after two `ended` events, so it looped. Loop off from 3 s: stopped at 6.0 s paused. | Pass |
| F4 | Play at 200 s, Ctrl-wheel zoom to 116.7 to 123.3 s, Shift-wheel pan to 0 to 6.6 s with the playhead far outside, then `+` from the keyboard: view 1.1 to 5.5 s (stays inside the old view). | Pass |
| F5 | Counting `clearRect` calls: 10 zoom clicks in one task and a single click each give 3 (waveform, ruler, energy, one frame). Canvas `width` setter calls stay 0 across repeated zooms at an unchanged size. | Pass (proxy: draw calls counted, not frame times) |
| F6 | Pure test only (`selectionFromField`). Browser: fields edited one at a time keep the stale banner behaviour correct (hidden when restored to 158.4, shown at 150). A fractional drag-then-edit was not driven. | Pass (unit) / not checked (drag-then-edit) |
| F7 | Track when already fitted, and zoom at the clamp: no live-region message. Programmatic focus restore and window refocus were not driven (window refocus ordering is assumed, not observed). | Pass (no-op cases) / not checked (refocus) |
| F8 | After a click on block 3 the strip tab stops are -1,-1,0,-1; after a click on a lane cell for block 1 they are 0,-1,-1,-1. | Pass |
| F9 | `role="group"` and `aria-keyshortcuts` are asserted in the contract test; zoom keys are named in the waveform caption. Screen-reader name exposure not checked. | Pass (markup) |
| F10, F11 | MutationObserver on `#liveStatus` (two observers were attached by the harness, so entries doubled; one announcement per action): the stale banner text was announced when End changed 158.4 to 150, and the banner cleared again at 158.4. Joining is covered by the announcer unit test. | Pass (proxy: DOM text, not speech) |
| F12 | Focus on a theme button at 1400 px, narrow to 1000 px: focus lands on the More button (the control is inside the closed menu). Open More, focus the theme button, widen to 1400: focus stays on that button. | Pass |
| F17, F18 | Copy covered by contract tests. Single render on analysis completion is by construction (view set first, then one full render that cancels the pending frame); not separately counted in a browser. | Pass (test) / not measured (F18) |
| Rows 17, 20 to 23 | `scrollWidth - clientWidth` is 0 at 390, 1024, 1280. Playhead and block start 1 px apart (a constant 1 px playhead offset) at three zoom levels at the centre and at two levels off-centre. Selection fields unchanged by zoom. `+` and `-` typed into the notes field and Start do not zoom or announce. Four rapid zoom clicks give one announcement. | Pass |

One wording note for the Measured row: the earlier window refocus handling (`quietFocus`) relies on the window `focus` event arriving before the element focus event in the same task. That was not observed.
