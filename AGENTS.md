# Working on lungecuta

Instructions for any agent working in this repository. Read this file first; it holds
what the design documents assume you already know.

**Then read `AGENTS.local.md` if it exists next to this file.** It is gitignored and
exists only on the maintainer's machine: the environment, where the sample media and the
related private repositories are, and which sample each Phase 0 test uses. Without it,
the Phase 0 media tests need samples of your own (see Conventions).

## Where things stand

- Decisions D1 to D15 are made (D10, the licence: MIT and Pickapicka's rules for model
  weights; D15, the rules written with M1 to M7's first parts, confirmed 2026-10-13); the
  rules D12 and the open decisions below mark *proposed* await the user's confirmation.
- **Phase 0's code is written (tasks 1 to 7)**: the package, `lungecuta init`, `lungecuta schema`, the
  tick unit and the time literals (`ticks.py`, `literals.py`); `lungecuta detect`, `lungecuta root`,
  `lungecuta relink` and `lungecuta import` with the decoder check, the proxy and its parity test (the
  proxy built from lungecuta's own decode); `lungecuta still`, `lungecuta sheet`
  and `lungecuta scope` for one clip (`still.py`, `sheet.py`, `scope.py`, `geometry.py`,
  `images.py`); music import with its WAV and beat map, and `lungecuta beats` (`music.py`,
  `beats.py`); the Phase 0 comp format, `lungecuta validate` and `lungecuta fcpxml` (`schema/comp.py`,
  `comp/`, `fcpxml.py`, `validate.py`); `lungecuta graphic`, `lungecuta patch-card` and `lungecuta patches`
  (`graphics.py`, `patches.py`). Frames are decoded from the decoder's own planes: chroma
  upsampled by linear interpolation where the stream says it sits, then ITU-T H.273, in
  numpy; ffmpeg's scale filter is not used to decode, since it was found off by a gain and
  inconsistent in its bit depth conversion (`decode.py`, `bench/swscale_range.py`).
  The proxy format and its gate are *provisional* (lossless FFV1 4:4:4; see open
  decisions).
- **Phase 1's spike is measured** (`bench/spike/`, 2026-10-13): latency, cache refill,
  on-screen colour and graphics parity meet their criteria on CPU OpenColorIO with PyAV
  reading the proxy; colour against Resolve and data levels per body need a chart and
  the cameras, and are the person's. The user asked on 2026-10-13 to carry on to the end
  rather than stop at each gate, so M0 to M2 work continues while those wait; nothing
  claims a milestone's exit criteria until they are met.
- **M2 has started**: `lungecuta render` renders Phase 0's comp format (one picture track of cuts
  at constant speed, music) to `master-prores-hq`, `web-h264`, `web-hevc10` or `prores-4444-alpha`
  (straight alpha, no audio) with a version manifest,
  and `lungecuta verify` checks a render against its comp (`compose.py`, `mixdown.py`, `render.py`, `encode.py`,
  `verify.py`), with layer transforms and eased keyframes (`keyframes.py`, `geometry.py`),
  overlapping layers composited in stack order, graphics layers as top overlays (also
  written to FCPXML as ProRes 4444), frames rendered several at a time, time remap keys
  (eased, forwards or backwards; to FCPXML as a time map) and `nearest` and `blend`
  retiming (blends in linear light), and the master audio chain (`audio.py`: high-pass,
  loudness to the preset's target, true-peak limiter, the ceiling held in the delivered
  AAC; `lungecuta verify` checks loudness and true peak by EBU R 128's tolerance). M1's clip
  grade is written too (`grade.py`: OCIO's primary grade with its controls in DI code
  values, ASC CDL, LUTs with declared spaces, on a footage layer's `grade`, as a list or a
  node graph whose nodes take windows and an HSL qualifier in OCIO's HSY), and M2's
  minimal style instance (`schema/style.py`: linked speed and scale tracks from
  `styles/`, the speed integrated exactly; `lungecuta op style`), and looks (styles holding a
  grade, linked from a node: `lungecuta op look`), and motion blur of a layer's own movement
  over its shutter (`motion_blur.shutter_deg`). Not written yet: flow retiming and the
  vector blur of motion inside footage, parallel nodes, FCPXML transforms. Graphics under
  footage render through the inverse output view; the editor does not draw them yet.
- **M3 has started**: `lungecuta serve` opens the editor (`server.py`, `preview.py`,
  `journal.py`, `web/`): the viewer on proxies through PyAV, the timeline with the beat
  grid, markers, audio waveforms and a range marked with I and O (which the agent sees),
  playback against the audio output clock, move, trim, split and ripple delete by drag and
  keys through `lungecuta op`'s functions, the journal with undo, redo and
  `lungecuta changes`, file watching, graphics drawn live from a sandboxed origin, the
  inspector (transform keys and eases, time remap, styles, the primary grade) and the
  graph editor (speed, source time, position, scale, opacity, rotation and gain, their
  keys dragged, and the speed graph's segments), each edit an `lungecuta op` operation
  (`key`, `unkey`, `set`, `remap`, `speed`, `style`, `grade`, `masks`, `transition`), and
  scopes of the frame on screen (on the proxy). Not written yet: the colour page's node
  graph.
- **The agent sees a comp without rendering it**: `lungecuta still COMP --at P`, `lungecuta sheet COMP
  --every b:1` and `lungecuta scope COMP --at P` show the frame the render delivers, labelled with
  its beat and the layers on screen (`look.py`). `lungecuta init` writes a project's own
  `AGENTS.md` and `CLAUDE.md`, the guide Codex and Claude Code read there, and
  `lungecuta guide --write` updates it (`guide.py`); keep it in step with the CLI, which
  `tests/test_guide.py` checks. `lungecuta status` describes a project, `lungecuta commands` the CLI as
  JSON, and `lungecuta mcp` serves every command as an MCP tool (`mcp_server.py`), started for
  Claude Code by the project's `.mcp.json` and for Codex by its `.codex/config.toml`.
- **ffmpeg is one pinned build** (2026-10-14, the user's choice): FFmpeg 9.0.2 from
  Martin Riedl's static macOS arm64 release, fetched on first use into
  `~/Library/Caches/lungecuta/` and checked by SHA-256 (`tools.py`); ffmpeg on the PATH is
  never used. `lungecuta setup` fetches it, Playwright's headless Chromium and the beat
  model ahead; `lungecuta doctor` checks the machine (`runtime.py`). Its decodes of the
  S-Log3 and HLG samples are identical to Homebrew 8.1's, frame for frame; the music
  sample's MP3 decodes differently by at most 7.2e-7 (`bench/ffmpeg_builds.py`).
- **A person who does not program starts with one sentence** to Claude Code or Codex,
  asking it to install lungecuta from the repository's URL: the README's "Installing, for
  the agent" gives each step and its check, and `lungecuta setup` writes the lungecuta skill
  (`skill.py`) into `~/.claude/skills/` and `~/.agents/skills/`, which takes the agent to a
  first project; keep it in step with the CLI, which `tests/test_skill.py` checks.
- **The time an edit takes is logged**: every lungecuta command run in a project, with its
  time, duration, parameters and outcome (`activity.py`, `cache/lungecuta/commands.jsonl`), and
  `lungecuta stages` turns that and the editor's journal into minutes by stage, for Phase 0's
  exit criterion (`stages.py`; the counting rule is *proposed*).
- **M5 and M7 have started**: transitions (`dissolve`, `dip`, `fade_in`, `fade_out` in
  the working space, `lungecuta op transition`), precomps (`comp` layers, recursive, composited
  in the working space), layer rotation, masks (rectangles and ellipses, feathered,
  `lungecuta op masks`), audio layer gain in dB with keys and fades, `lungecuta audio-check` (cut
  offsets to beats and to onsets by SuperFlux, `onsets.py`; RMS per half beat), `lungecuta lint`
  (five of the review rules), `lungecuta flash`, `lungecuta diff` and `lungecuta licence`.
- **Two edits made as an agent makes one** run end to end on the samples
  (`bench/dry_run.sh`, `bench/dry_run2.sh`; their stage times in `bench/RESULTS.md`): a cut
  built one `lungecuta op place COMP KEY --at P --for SPAN --source T` (or `--to P`) per shot,
  splits, slips, styles, looks, a dissolve, a title by `lungecuta op graphic`, sounds by
  `lungecuta op sfx`, a remap ramp, then lint, audio-check, sheet, render, verify and flash. What
  they tripped over is fixed and tested. Import, render, verify and sheet were profiled
  and made faster with outputs identical (`bench/import_cost.py`, `render_stages.py`,
  `verify_cost.py`).
- **What Phase 0 still needs is the person:** the Resolve checks in
  [docs/research/phase0-resolve-checks.md](docs/research/phase0-resolve-checks.md)
  (markers, in points against timecode, speed, the float WAV, the graphics card), and the
  real edit with its time recorded by stage, which is the exit criterion.
- The next work is **Phase 0** in [docs/design/10-roadmap.md](docs/design/10-roadmap.md),
  broken into tasks below. Phase 0's media code (time literals, detection, import,
  proxies, stills) is production code written to M0's standards, and M0 later continues
  it; everything else in M0 and later milestones waits until Phase 1 has met its exit
  criteria.
- "M0's standards" means: covered by tests, typed with pydantic where it touches the
  project files, no fallbacks, and every behaviour that was decided written down in
  `docs/design/` in the same change.
- What was decided is in [docs/design/decisions.md](docs/design/decisions.md); the review
  that corrected the first draft is in
  [docs/research/design-review-2026-10-10.md](docs/research/design-review-2026-10-10.md),
  and the second review, with the findings still open, is
  [docs/research/design-review-2026-10-11.md](docs/research/design-review-2026-10-11.md).
  When something is not written down, ask rather than infer.
- Read in this order: [README.md](README.md),
  [docs/design/00-overview.md](docs/design/00-overview.md),
  [docs/design/10-roadmap.md](docs/design/10-roadmap.md),
  [docs/design/decisions.md](docs/design/decisions.md), then the topic documents a task
  touches, and [bench/RESULTS.md](bench/RESULTS.md) for every number.

## Rules

These come from the user and from mistakes already made in this project. They are not
optional.

- **Research before computing.** Primary sources first (vendor white papers, ITU, OCIO
  docs). An implementation is a secondary source.
- **Never invent a constant.** If a curve, gamut or threshold is not published or
  measured, say so and stop. DJI D-Log M and D-Log 2 are applied only through DJI's own
  LUTs for this reason.
- **Label what is unverified.** Write which part of a claim is verified and which is not.
- **Every number has a script.** A measurement lives in `bench/` with the command that
  produced it, and its result in `bench/RESULTS.md` with machine, versions and date.
  Numbers measured elsewhere are quoted with their origin (the last section of
  `bench/RESULTS.md`, where a private origin is labelled as such) and re-measured here
  before a decision rests on one.
- **A benchmark checks its output.** Count the frames that came out and compare hashes
  before quoting a speed. The first draft of this design reported a decoder that dropped
  12 of 705 frames as merely "slower" (D9).
- **Fail fast.** No generic `try`/`except`, no fallback logic, `assert` for validation, no
  silent sanitizing. A clip whose colour space cannot be identified is imported as
  **undecided**, with the evidence found, and nothing can use it until it is decided; it is
  never given a guessed colour space.
- **Python through uv.** Python 3.12 or later in the project venv, never system Python.
  One-off scripts: `uv run --with <pkg> python ...` or a PEP 723 header, as in `bench/`.
- **English** in code, comments, documents and commit messages. **No em dash character**
  anywhere; use a colon, comma or parentheses.
- **Commits**: conventional messages, no AI attribution or signatures. **Do not commit or
  push unless the user asks.**
- **Committed files hold nothing personal.** The repository is public. No absolute
  paths, no names of sample files or their folders, nothing copied from private
  repositories. Refer to those by role ("the existing ingest scripts", "the S-Log3
  sample", "past projects P1 to P4"); the details belong in `AGENTS.local.md` or
  `docs/research/private/`, both gitignored. Before staging, grep what is about to be
  committed.
- **Project files never execute Python** (expressions and graphics code run in sandboxes,
  [docs/design/09-agent-interface.md](docs/design/09-agent-interface.md#sandboxing)).
- Footage and sample media are read in place and never copied into the repository.

## Conventions to follow, so they need not be decided again

Taken from [Pickapicka](https://github.com/son-engr-kr/pickapicka), the user's photo
editor:

- `pyproject.toml` with `requires-python = ">=3.12"`, build backend `uv_build`, code under
  `src/lungecuta/`, author Hyoungseo Son, MIT (`license = { file = "LICENSE" }`, D10).
- Model weights as Pickapicka takes them (D10): permissive code and weights on the default
  path, anything narrower an opt-in download that shows its terms, files fetched on first
  use and pinned by size and SHA-256, never committed.
- CLI with click; entry point `lungecuta = "lungecuta.cli:main"`.
- Tests with pytest in a `dev` dependency group, run as `uv run pytest tests/`.
- Dependencies at their current releases, pinned by `uv.lock`.

Decided in this repository rather than taken from Pickapicka:

- Schemas as pydantic models, with the generated JSON Schema committed under `schema/`
  ([docs/design/03-project-format.md](docs/design/03-project-format.md)).
- Tests that fetch a published document (Apple's FCPXML DTD) carry the marker `network`
  and run with `uv run pytest -m network`; the DTD is fetched, never committed.
- Tests of the commands that look at frames run on small ProRes clips that
  `tests/clips.py` makes with ffmpeg, with media records written by hand, so
  `uv run pytest tests/` covers them without samples.
- **Tests that need sample media** carry the pytest marker `media` and read the directory
  named by the environment variable `LUNGECUTA_SAMPLES`. Each test names the file it
  needs by a path relative to that directory, set in one place (a test fixture), so the
  committed tests name no sample. `uv run pytest tests/` runs the rest;
  `uv run pytest -m media` runs them, and a missing variable or file is a test failure,
  not a skip. The relative path of each role's file (`slog3`, `hlg`, `music`) is in
  `tests/samples.local.json`, gitignored, read by the `samples` fixture in
  `tests/conftest.py`; the S-Log3 role also carries its `creation_time`.

## Toolchain the measurements were made with

| item | version |
| --- | --- |
| ffmpeg | 9.0.2, Martin Riedl's static macOS arm64 release, pinned (`src/lungecuta/tools.py`; GPL build: call as a subprocess only). Measurements before 2026-10-14 used Homebrew's 8.1 |
| OpenColorIO | 2.6.0 from PyPI, built-in `studio-config-v5.0.0_aces-v2.1_ocio-v2.6` |
| PyTorch | 2.14.1, MPS available |
| Chromium | Playwright 1.63.0: chrome-headless-shell and Chrome for Testing, both 153.0.8010.12 |
| DaVinci Resolve | 21.0.4 free. Blackmagic's 21.1 notes: "Advanced scripting now requires DaVinci Resolve Studio" (secondary sources: Python scripting moved to Studio). Phase 0 uses File > Import > Timeline, never scripting |

The machine is in `bench/RESULTS.md`.

## Sample media

Needed for Phase 0, by role (the maintainer's files are listed in `AGENTS.local.md`):

- **the S-Log3 sample**: Sony a7C II (ILCE-7CM2), S-Log3 / S-Gamut3.Cine, H.264 4:2:2
  10-bit 140 Mbps, flagged full range, no matrix tag (XML says rec709), 705 frames, with
  its `M01.XML` sidecar. Clip A in `bench/RESULTS.md`.
- **the HLG sample**: Sony a7C II HLG, tagged bt2020 / arib-std-b67, XML
  `hlg-slog3ootf`. **Expected import result: undecided**, because no HLG input mapping
  exists yet.
- **the music sample**: any music file with a steady beat (the maintainer's is an MP3
  at 48 kHz, 124.9 s). It has no ground truth; the beat map is checked against itself
  and against a synthetic track whose beats are known.

Not on disk yet, and needed (ask the user): the music and footage chosen for the Phase 0
edit (its exit criterion is a real edit); an XAVC HS (HEVC) original; DJI clips in D-Log,
D-Log M and D-Log 2 (Osmo Pocket 4P), with the LUTs DJI ships for each; a lens-cap shot
and a colour chart from every body.

The user shoots a Sony ILCE-7CM2 (S-Log3, HLG) and DJI cameras including the Osmo
Pocket 4P (D-Log 2, 10-bit, 1x zoom only). Their recent footage is all S-Log3, so the HLG
input mapping is deferred (the user, 2026-10-13): HLG clips keep importing as undecided.

## Phase 0, as tasks

Exit criterion: one real music edit made end to end with these tools, its time recorded
by stage as [docs/design/10-roadmap.md](docs/design/10-roadmap.md#phase-0-the-baseline-days)
describes.

1. **Skeleton**: package, `lungecuta` entry point, pytest, the schema module. Time literals and
   the tick unit first, following the grammar in
   [docs/design/03-project-format.md](docs/design/03-project-format.md#literal-grammar),
   with round-trip tests across every rate listed there, source-time fields, layer time
   ([layer timing](docs/design/03-project-format.md#layer-timing)), and the rules for
   frames and samples
   ([from time to frames and samples](docs/design/03-project-format.md#from-time-to-frames-and-samples)).
   Then `lungecuta init`, with `media/`, `roots.local.json` and its `.gitignore`.
2. **`lungecuta detect` and `lungecuta import`**: the evidence chain of
   [docs/design/02-media.md](docs/design/02-media.md#colour-space-detection), Sony XML
   first. Media tests on the S-Log3 sample (expect the record in
   [docs/design/03-project-format.md](docs/design/03-project-format.md#media), with the
   file's own name, path, hash and creation time in place of the placeholders) and on
   the HLG sample (expect **undecided**, with the XML evidence listed). Records are
   written one per file under `media/`, with paths relative to a named root; the rates,
   recording mode and start timecode come from the XML too
   ([rates and timecode](docs/design/02-media.md#rates-and-timecode)). Include the
   per-clip decoder check (first 300 frames compared byte for byte, software against VideoToolbox) and
   the proxy with its own decode record. Run the proxy parity test on ProRes Proxy, LT and
   4444 at 540p on both samples before fixing the profile
   ([proxies](docs/design/02-media.md#proxies)), and put the results in `bench/RESULTS.md`.
3. **`lungecuta still --media KEY --at f:N`**: one frame through CPU OpenColorIO (explicit matrix
   and range, input transform, the project's output view), threaded by row bands as
   `bench/ocio_threads.py` does. Decode with the ffmpeg CLI, not PyAV, until PyAV has passed
   a framemd5 check against the CLI. The PNG is 8-bit and holds display-encoded code values
   for the project's display and view, which the command's help says; numbers (per-channel
   min, max and 1st, 50th, 99th percentiles in 10-bit output codes) come from the float
   frame and are printed as JSON. Decode with
   `-sws_flags accurate_rnd+full_chroma_int+full_chroma_inp`; default size is the clip's
   own; OCIO threads default to the machine's core count. Checking the seeked frame
   against a sequential decode is a `media` test, not something every call does.
4. **`lungecuta sheet --media KEY --every f:30` and `lungecuta scope --media KEY --at f:N`**: images plus
   the numbers defined in
   [docs/design/09-agent-interface.md](docs/design/09-agent-interface.md#scope-numbers),
   following that document's conventions for finding the project, writing outputs and
   printing JSON.
5. **Beat map**: Beat This! (D14; madmom is excluded by its model licence). Decode the
   music once to a 48 kHz PCM WAV in the cache; the analysis and the FCPXML both use that
   file. Re-analysis follows the rule in
   [beat maps](docs/design/03-project-format.md#beat-maps).
6. **FCPXML export** of a cut: cut points on beats, resolved to frames by the stated rule,
   constant-speed segments, markers, source in points written against each clip's start
   timecode.
   Read the FCPXML writer in [Jalara](https://github.com/son-engr-kr/jalara)
   (`crates/jalara-export/src/fcpxml.rs`) first. The user imports the result into Resolve
   and reports what arrived; an agent cannot operate Resolve's GUI. Find out on that
   import whether markers survive, whether variable speed arrives as an editable curve
   (`docs/research/resolve-notes.md`), and whether in points land on the intended source
   frames (a few cuts compared against `lungecuta still`).
7. **Graphics**: the past projects' web-page renderer producing ProRes 4444 with alpha,
   for import into Resolve (where it is: `AGENTS.local.md`). With it, render a card of
   known colour patches; the user puts it over footage in the DWG/DI timeline and exports,
   and the export's codes are read back to see whether Resolve tone-maps graphics.

## Open decisions (the user's)

- Phase 1 thresholds marked *proposed* in the roadmap.
- That a re-analysis moving downbeats is not applied either, like one moving beats
  (*proposed* with `lungecuta beats`).
- A source time between two frames shows the frame whose interval holds it, and markers
  land on the first frame at or after their time (*proposed*, D12; the FCPXML exporter
  and `lungecuta still` use them).
- Music goes to Resolve as a 32-bit float WAV (no clipping of a decode over full scale);
  whether Resolve takes it is a Phase 0 check.
- In comps: layers listed bottom first (a later layer over an earlier one); transform
  units (position and anchor as fractions, scale relative to the layer's pixels); a key's
  `ease` shaping the segment arriving at it in the CSS sense of the names, with After
  Effects' Easy Ease values (`src/lungecuta/keyframes.py`). All *proposed*.
- Delivery: x264 CRF 18 and AAC 256 kbps (the past deliverables' starting points),
  co-sited chroma decimation and triangular dither of one code (*proposed*,
  `src/lungecuta/encode.py`).
- Chroma is upsampled by linear interpolation from the stream's stated siting, and 4:2:2
  proxy candidates are decimated with a co-sited [1, 2, 1] / 4 filter (*proposed*; a
  sharper filter would differ only at chroma edges).
- The rules D12 marks *proposed*: where a cut lands between frames, audio rounding, the
  re-analysis rule, overlapping music layers, and S&Q clips.
- Which source frame a source time between two frames shows; `lungecuta still --media` refuses
  such a time until it is decided
  ([03-project-format.md](docs/design/03-project-format.md#from-time-to-frames-and-samples)).
- The findings left open in
  [docs/research/design-review-2026-10-11.md](docs/research/design-review-2026-10-11.md).
- A clip's own sound (`sound` on the footage layer, `lungecuta op sound`): a field of the
  footage layer rather than a linked audio layer, and played only at 100 % (*proposed*,
  `docs/design/07-audio.md#location-sound`).
- `lungecuta op speed` (the speed graph's constant segment): the rest of the layer keeps its
  speeds and its in and out points stay, so the change does not ripple the comp
  (*proposed*, `docs/design/06-editing.md#speed`).
- The `web-hevc10` preset: VideoToolbox at `-q:v 70`, the lowest setting with web-h264's
  PSNR on every plane, on the grainy check comp (`bench/RESULTS.md`), *proposed*; it
  smooths a little more grain than web-h264, which the person judges on screen.
- The flash rule's standard: `lungecuta flash` applies WCAG 2.2's general flash threshold (read,
  defined for web pages at 1024x768, mapped onto a video frame as *proposed* in
  `src/lungecuta/flash.py`), since the deliverables are for the web; ITU-R BT.1702 for
  television is not read (its site and Ofcom's refuse automated downloads). Red flashes
  are not checked.
- **The proxy format and its parity criterion** (D7, amended): no ProRes profile passed;
  lossless FFV1 4:4:4 passed at 19 times the size and is used provisionally, with the gate
  in R'G'B' codes because 2 DI codes cannot be reached by a proxy downscaled before the
  input transform. Options: keep FFV1, store proxies in the working space, or restate the
  criterion ([docs/design/02-media.md](docs/design/02-media.md#proxies)).

## Known unknowns

Each is labelled where it is cited; collected here so none is mistaken for a fact:

- D-Log 2 curve and gamut, and where DJI's D-Log 2 LUT is published
- whether the Osmo Pocket 4P's D-Log mode is the published 2017 D-Log / D-Gamut (check a grey scale against codes 95, 408, 586)
- D-Log M LUT input normalization, and how much of it is a look
- what Sony means by `hlg-slog3ootf`
- data levels and matrix per camera body (needs the chart and lens-cap shots)
- VideoToolbox correctness on a real XAVC HS original
- how OCIO's tone-zone ranges (`GradingToneTransform`) map onto DaVinci Intermediate (the primary grade's mapping is settled, `04-color.md`)
- what reaches the screen in Chrome, QuickTime and Safari (screen capture, not decode APIs)
- the HLG input mapping (what to do with `hlg-slog3ootf` clips; deferred by the user, 2026-10-13)
- RIFE against Resolve free's Optical Flow on the user's footage
- VideoToolbox H.264 encode quality against x264
- PyTorch MPS numerical agreement with numpy references
- whether Speed Warp is Studio-only (`docs/research/resolve-notes.md`)
- whether Resolve places FCPXML in points by the clip's timecode
- whether Resolve sends a Rec.709 graphic in a DWG/DI timeline through the output tone mapping
- what the Sony XML says for slow-and-quick recording (`captureFps` against `formatFps`)
- whether Resolve imports FCPXML markers, places in points by the clip's timecode, keeps a
  sub-frame audio in point, reads a 32-bit float WAV, and which alpha mode and input colour
  space it gives a ProRes 4444 graphic (all in the Phase 0 checks)
- whether swscale's chroma upsampling (used for 4:2:2 and 4:2:0 sources) sites chroma the
  way the camera did; flat areas are unaffected, edges are not checked
- which licence PyAV's wheel is under: it calls its FFmpeg libraries LGPL v3 but ships
  libx264 and libx265 ([08-deliver.md](docs/design/08-deliver.md#licences))
