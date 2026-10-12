# Working on lungecuta

Instructions for any agent working in this repository. Read this file first; it holds
what the design documents assume you already know.

**Then read `AGENTS.local.md` if it exists next to this file.** It is gitignored and
exists only on the maintainer's machine: the environment, where the sample media and the
related private repositories are, and which sample each Phase 0 test uses. Without it,
the Phase 0 media tests need samples of your own (see Conventions).

## Where things stand

- Decisions D1 to D9 are made; D10 (licence) is open. **No code exists yet.**
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
  [docs/research/design-review-2026-10-10.md](docs/research/design-review-2026-10-10.md).
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
  `src/lungecuta/`, author Hyoungseo Son. Leave the licence field unset until D10 is
  decided.
- CLI with click; entry point `lc = "lungecuta.cli:main"`.
- Tests with pytest in a `dev` dependency group, run as `uv run pytest tests/`.
- Dependencies at their current releases, pinned by `uv.lock`.

Decided in this repository rather than taken from Pickapicka:

- Schemas as pydantic models, with the generated JSON Schema committed under `schema/`
  ([docs/design/03-project-format.md](docs/design/03-project-format.md)).
- **Tests that need sample media** carry the pytest marker `media` and read the directory
  named by the environment variable `LUNGECUTA_SAMPLES`. Each test names the file it
  needs by a path relative to that directory, set in one place (a test fixture), so the
  committed tests name no sample. `uv run pytest tests/` runs the rest;
  `uv run pytest -m media` runs them, and a missing variable or file is a test failure,
  not a skip.

## Toolchain the measurements were made with

| item | version |
| --- | --- |
| ffmpeg | 8.1, Homebrew (GPL build: call as a subprocess only) |
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

Not on disk yet, and needed (ask the user): the music and footage for the Phase 0 edit
(its exit criterion is a real edit); an XAVC HS (HEVC) original; DJI clips in D-Log,
D-Log M and D-Log 2 (Osmo Pocket 4P), with the LUTs DJI ships for each; a lens-cap shot
and a colour chart from every body.

The user shoots a Sony ILCE-7CM2 (S-Log3, HLG) and DJI cameras including the Osmo
Pocket 4P (D-Log 2, 10-bit, 1x zoom only).

## Phase 0, as tasks

Exit criterion: one real music edit made end to end with these tools, its time recorded.

1. **Skeleton**: package, `lc` entry point, pytest, the schema module. Time literals and
   the tick unit first, following the grammar in
   [docs/design/03-project-format.md](docs/design/03-project-format.md#literal-grammar),
   with round-trip tests across every rate listed there. Then `lc init`.
2. **`lc detect` and `lc import`**: the evidence chain of
   [docs/design/02-media.md](docs/design/02-media.md#colour-space-detection), Sony XML
   first. Media tests on the S-Log3 sample (expect the record in
   [docs/design/03-project-format.md](docs/design/03-project-format.md#media), with the
   file's own name, path, hash and creation time in place of the placeholders) and on
   the HLG sample (expect **undecided**, with the XML evidence listed). Include the
   per-clip decoder check (first 300 frames, framemd5, software against VideoToolbox) and
   the proxy with its own decode record.
3. **`lc still --media KEY --at f:N`**: one frame through CPU OpenColorIO (explicit matrix
   and range, input transform, the project's output view), threaded by row bands as
   `bench/ocio_threads.py` does. Decode with the ffmpeg CLI, not PyAV, until PyAV has passed
   a framemd5 check against the CLI. The PNG is 8-bit and holds display-encoded code values
   for the project's display and view, which the command's help says; numbers (per-channel
   min, max and 1st, 50th, 99th percentiles in 10-bit output codes) come from the float
   frame and are printed as JSON. Decode with
   `-sws_flags accurate_rnd+full_chroma_int+full_chroma_inp`; default size is the clip's
   own; OCIO threads default to the machine's core count. Checking the seeked frame
   against a sequential decode is a `media` test, not something every call does.
4. **`lc sheet --media KEY --every f:30` and `lc scope --media KEY --at f:N`**: images plus
   the numbers defined in
   [docs/design/09-agent-interface.md](docs/design/09-agent-interface.md#scope-numbers),
   following that document's conventions for finding the project, writing outputs and
   printing JSON.
5. **Beat map**: Beat This! or madmom for beats and downbeats; check each one's licence
   before depending on it.
6. **FCPXML export** of a cut: cut points on beats, constant-speed segments, markers.
   Read the FCPXML writer in [Jalara](https://github.com/son-engr-kr/jalara)
   (`crates/jalara-export/src/fcpxml.rs`) first. The user imports the result into Resolve
   and reports what arrived; an agent cannot operate Resolve's GUI. Find out on that
   import whether markers survive and whether variable speed arrives as an editable curve
   (`docs/research/resolve-notes.md`).
7. **Graphics**: the past projects' web-page renderer producing ProRes 4444 with alpha,
   for import into Resolve (where it is: `AGENTS.local.md`).

## Open decisions (the user's)

- Licence: MIT proposed (D10).
- Phase 1 thresholds marked *proposed* in the roadmap.

## Known unknowns

Each is labelled where it is cited; collected here so none is mistaken for a fact:

- D-Log 2 curve and gamut, and where DJI's D-Log 2 LUT is published
- whether the Osmo Pocket 4P's D-Log mode is the published 2017 D-Log / D-Gamut (check a grey scale against codes 95, 408, 586)
- D-Log M LUT input normalization, and how much of it is a look
- what Sony means by `hlg-slog3ootf`
- data levels and matrix per camera body (needs the chart and lens-cap shots)
- VideoToolbox correctness on a real XAVC HS original
- how OCIO's LOG-style grading defaults map onto DaVinci Intermediate
- what reaches the screen in Chrome, QuickTime and Safari (screen capture, not decode APIs)
- whether PyAV's bundled FFmpeg decodes identically to the Homebrew ffmpeg CLI
- the HLG input mapping (what to do with `hlg-slog3ootf` clips)
- RIFE against Resolve free's Optical Flow on the user's footage
- VideoToolbox H.264 encode quality against x264
- PyTorch MPS numerical agreement with numpy references
- whether Speed Warp is Studio-only (`docs/research/resolve-notes.md`)
