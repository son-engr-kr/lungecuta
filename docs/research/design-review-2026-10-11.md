# Second design review (2026-10-11)

A read-through of `docs/design/` after the first review (D9), looking for what Phase 0 and
M0 would have to decide while writing code. Findings are listed with what was checked and
what was done. Adopted findings are summarized in `docs/design/decisions.md`, D11 to D14.

## Adopted

| finding | checked | outcome |
| --- | --- | --- |
| beat indices were not stable: `b:0` was the first beat after the music layer's in point, so one beat gained or lost in a re-analysis shifted every later beat literal, and `manual` beats were keyed by index | design inspection | re-analysis keeps every index or is not applied, *proposed* (D12) |
| cutting music on bars into several layers ([07-audio.md](../design/07-audio.md#music-editing)) could not be described by a grid taken from one layer | design inspection | the comp grid comes from several music layers (D12) |
| no rule for where a cut lands when its time falls between frames, and audio at the 1000/1001 rates cannot be sample-exact at frame boundaries (1601.6 samples per frame at 29.97p and 48 kHz) although the literal grammar said "never rounded" | computation; past edits cut on the first frame after the beat | first frame at or after for cuts, nearest sample for audio, *proposed* (D12) |
| `f:` was defined only at the comp's rate, and source time had no origin | the S-Log3 sample: start timecode 00:00:21:15 in the data stream tag and in the XML `LtcChangeTable` | source-time fields, first presented frame as 0, start timecode kept (D12) |
| the Sony XML's rates were not read | all three XML files checked carry `<VideoFrame captureFps formatFps>` and `<RecordingMode>`; no S&Q file on disk | read and stored; a rate mismatch stays undecided until an S&Q file confirms the fields (D12) |
| the beat tracker was still open between Beat This! and madmom | both READMEs: Beat This! code and weights MIT; madmom models and data CC BY-NC-SA 4.0 | Beat This! (D14) |
| Resolve decoding compressed music itself could place it differently from ffmpeg's decode the beats were found in | not measured | music decoded once to PCM WAV for analysis, mix and FCPXML (D14) |
| a graphic in a Resolve DWG/DI timeline may go through the output tone mapping, changing the baseline's title colours | not checked in Resolve | Phase 0 test with a card of known patches ([10-roadmap.md](../design/10-roadmap.md#phase-0-the-baseline-days)) |
| the proxy profile was chosen by speed and size, but must pass a parity test nobody had run on it | PSNR and parity not yet measured | Proxy, LT and 4444 tested in Phase 0 (D7 amended) |
| the go/no-go compares against one timed Phase 0 edit, with no breakdown | design inspection | time recorded by stage, with the material described, and a running baseline, *proposed* |
| media paths were absolute in a project meant to be committed | design inspection | named media roots and `lc relink` (D13) |
| media records inside `project.json` meant every import rewrote the file being edited | design inspection | one file per record under `media/` (D13) |
| keyframe times were comp time without saying so, so moving a layer left its keys behind | design inspection | After Effects layer timing, decided by the user (D11) |
| the edit operations were said to be callable from `lc` but no command existed | `06-editing.md` against the command table | `lc op` (D13, [09-agent-interface.md](../design/09-agent-interface.md#commands)) |
| the content-hash rule kept the UI from overwriting the agent, not the reverse; the agent had no way to see the person's changes before M8's `lc diff`; undo was never mentioned | design inspection | UI journal, `lc changes`, undo on the journal (D13) |
| the preview cache key had no versions, and its size was not considered | 1.56 MB per 540p RGB frame, 2.8 GB per minute at 30p | versions in the key and a render manifest; storage decided in Phase 1 (D13) |

Also clarified on the way: a linked style instance's compiled remap keys are derived data
in the cache, and are written into the file only when the instance is detached.

## Open, for Phase 1

| finding | why it matters | proposal |
| --- | --- | --- |
| audio-to-picture sync in the viewer is not a criterion anywhere | a music editor whose preview is out of sync cannot judge a cut against a beat | measure a click and flash pattern by screen and loopback audio capture; make the offset a kill criterion |
| in the reference monitoring mode, engine frames are converted for the display but live DOM overlays are not | code 0.502 is shown as linear 0.216 (sRGB) in the viewer, 0.191 on the BT.1886 reference and 0.259 in the Mac mode, so the person sees a different title from the one rendered, while a code-value diff passes | send engine frames as display code values, composite the live overlay, and apply one monitoring transform to the result; measure it with the on-screen criterion |
| the latency criterion is only at 540p, and "540p" is undefined for a vertical comp; with a 1080-line proxy the input transform and grade run on four times the pixels before the crop | 9:16 cut from 16:9 is a main use | add that case and uncached scrubbing; define preview size by pixel count |
| "colour against Resolve" does not say where the two are compared | the default ACES 2.0 view cannot match Resolve's DaVinci tone mapping | compare in DWG/DI after the input transform, Resolve's CST tone mapping off, exported at 16 bits |

## Open, for scope

| finding | proposal |
| --- | --- |
| variable frame rate: phones and screen recordings are in scope, and a past project looked frames up by timestamp, but nothing handles VFR | detect at import from timestamps; refuse, or conform to a constant-rate derived file with its own record |
| word timings for subtitles came from Whisper in past projects; no command produces them | a transcription command whose word timings are data in the project |
| the audio of retimed footage is not defined | mute by default, or follow constant speed with stated pitch handling |
| the local server's write API is reachable from any page in the user's ordinary browser (DNS rebinding, cross-site requests), and graphics code in the editor's browser could call it if served from the same origin | check the Host header, require a per-session token, serve graphics from another origin in a sandboxed frame without `allow-same-origin` |
| M2's "matches that deliverable frame by frame at every cut" is undefined: the past deliverables were graded and encoded differently, so pixels cannot match | define it as the same source frame at each output frame around every cut |
