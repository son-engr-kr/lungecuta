# Agent interface

The agent edits the project's JSON files directly; nothing about that needs a special
protocol. What it needs from lungecuta is **eyes and checks**: ways to see a frame, a
stretch of timeline, a scope or a loudness curve, and ways to find out whether the
project still means what it should. Everything here is a CLI command first, and `lungecuta mcp`
is a thin wrapper over the same commands: each an MCP tool over stdio, its input schema
its arguments and options, its result what it prints, and the pictures it writes returned
as images no larger than 1568 pixels on the long side, the size Anthropic's vision
documentation scales larger images down to (`src/lungecuta/mcp_server.py`). A command
that fails returns its error and traceback as the tool's error; the server keeps running.

## Commands

| command | output |
| --- | --- |
| `lungecuta init DIR` | a new project directory: `project.json` with the colour defaults of [03-project-format.md](03-project-format.md#projectjson), `proxy_height` 540, `media_roots` `[]`, `comps` `[]`, `deliver` `[]`; empty `media/`, `comps/`, `grades/`, `styles/`, `graphics/`, `luts/`, `cache/`; `roots.local.json` `{}` and a `.gitignore` naming it and `cache/`; the agent guide (`AGENTS.md`, `CLAUDE.md`); refuses a non-empty directory. The `.lc` suffix is a convention, not required |
| `lungecuta status` | what is in the project for an agent arriving: media (footage with its colour space and whether it is usable, music with its tempo and licence), comps (size, rate, length, layers by type, markers, transitions), the editor changes `lungecuta changes` has not shown, and each render in the cache with whether its comp and media still match (`src/lungecuta/status.py`) |
| `lungecuta commands` | every command as data: its summary, arguments and options (flags, types, defaults, choices, whether required), the `op` group's operations among them, so an agent reads the CLI without parsing help text |
| `lungecuta mcp [--project DIR]` | every command above (but `serve`) as an MCP tool on stdio, named by its words (`still`, `op_place`; the server's name, `lungecuta`, is their namespace), defaulting to the project; the guide is the server's instructions; a project's `.mcp.json` starts it for Claude Code and its `.codex/config.toml` for Codex |
| `lungecuta doctor` | whether this machine can run lungecuta: the platform, the pinned ffmpeg build (hashed), Playwright's headless Chromium, Beat This!'s checkpoint and the `lungecuta` command on the PATH, each with what was found and what to run when it fails; exit status 1 when one does (`src/lungecuta/runtime.py`) |
| `lungecuta setup` | fetches what `doctor` finds missing: the pinned ffmpeg build ([08-deliver.md](08-deliver.md#licences)), the headless Chromium and the checkpoint, each otherwise fetched on first use; installers' progress goes to stderr |
| `lungecuta guide [--write]` | the guide a coding agent reads in a project (below); `--write` puts it into the project's `AGENTS.md` between its markers and makes `CLAUDE.md` import it |
| `lungecuta import FILES...` | media records, one file each under `media/`: probe, rates and timecode, colour decision with evidence, decoder check, proxy, PCM WAV and beat map for music, or with `--sound` a PCM WAV and no beat map (effects, voices). Each file must lie under a root named with `--root NAME` |
| `lungecuta beats KEY` | runs the beat analysis of a music record again on its WAV and writes the new times only if every beat and downbeat index is kept; otherwise lists the inserted and removed beats and exits with status 1, and `--replace` applies it anyway ([03-project-format.md](03-project-format.md#beat-maps)) |
| `lungecuta root NAME DIR` | lists media root NAME in `project.json` and places it at DIR in `roots.local.json` |
| `lungecuta relink --root NAME DIR` | finds missing media under a new directory by size, then hash, and points the root or the record at it |
| `lungecuta detect FILE` | the colour-space decision and every piece of evidence considered, without importing |
| `lungecuta validate` | schema and meaning checks ([03-project-format.md](03-project-format.md#validation)); in Phase 0 every file against its schema, every original unchanged since import, every comp resolved, and the undecided clips listed |
| `lungecuta fcpxml COMP` | FCPXML 1.10 of a comp for Resolve's File > Import > Timeline (Phase 0): cuts on one picture track, constant speed as a time map, music as the WAV its beats were found in, markers; prints every cut's comp frames, timeline timecode, source frames and source timecode, and the `lungecuta still` that shows its first frame ([10-roadmap.md](10-roadmap.md#phase-0-the-baseline-days)) |
| `lungecuta still COMP --at P` | PNG of the frame `lungecuta render` delivers at a comp position (the first frame at or after it), graphics included, at the comp's size; `--height` area-averages that picture on its display-encoded values, as a player shows a smaller copy, and the numbers (as for `lungecuta still --media`) come from the full frame; the JSON gives the frame, its seconds, its beat coordinate and the layers on screen (`src/lungecuta/look.py`) |
| `lungecuta still --media KEY --at f:N` | the same for one imported clip, with no comp needed (Phase 0's form): read from the original with the record's matrix and range, through its input transform into the working space, turned by its rotation tag, area-downscaled in the working space with `--height`, and through the project's display and view on CPU OpenColorIO (`--threads`, by default the core count). The PNG is 8-bit and untagged; the numbers are per-channel minimum, maximum and 1st, 50th and 99th percentiles in 10-bit output codes. A clip undecided in colour has no still |
| `lungecuta sheet COMP --every SPAN [--from P --to P]` | contact sheet of a comp: a frame every `b:1` (stepping along the beat grid from beat 0 or `--from`, so the comp before the first beat is not in it), `f:30`, or `0.1s` for the dense timeline sheet that catches timing bugs stills miss; each tile is `lungecuta still COMP`'s frame, labelled with its frame, seconds, beat and the layers on screen, which the JSON lists with each tile's numbers |
| `lungecuta sheet --media KEY --every f:30` | contact sheet of one clip, frames labelled with frame number and seconds (Phase 0's form): a frame at every interval from frame 0 (`f:N`, or seconds that are a whole number of frames), from one decode of the original through the same chain as `lungecuta still`, each area-downscaled to the tile in the working space; tiles 320 pixels on the long side unless `--height` says otherwise (never enlarged), six columns and six rows a page (`--columns`, `--rows`), labels in a strip below each tile; the JSON lists every tile with `lungecuta still`'s numbers for it |
| `lungecuta framing COMP LAYER [--at P]` | where the comp's frame sits in a footage layer's own frame: its corners taken back through the layer's transform, in fractions of the frame as shown, on the first and last visible frames and at each `--at`; every run of frames where the comp shows past the source's edge (an exact test: the view is a parallelogram in a rectangle; masks, opacity and transitions not counted); a PNG of the source frame with a 0.1 grid and those windows (`src/lungecuta/framing.py`) |
| `lungecuta face-audit FILE` | every face the detector still finds in a render, as a crop sheet with scores |
| `lungecuta graphic PAGE --size WxH --rate R --frames N --out F.mov` | a web page with the past pages' contract (`window.ready`, `window.renderAt(t)`) rendered frame by frame to ProRes 4444 with alpha, BT.709 limited range, tagged 1-1-1; the page is served only its own directory (Phase 0's graphics, [05-motion-graphics.md](05-motion-graphics.md#phase-0-graphics-for-resolve)) |
| `lungecuta patch-card DIR --size WxH --rate R` | a card of 27 known colour patches as `card.json`, `card.html` and `card.mov`, its own file read back first |
| `lungecuta patches FILE --card card.json --at N` | the mean R'G'B' of each patch's middle in a frame of FILE (an export from Resolve), against the card's codes |
| `lungecuta lint COMP` | taste and rights rules (below) as warnings. Written so far: graphics on screen at least `lint.graphics_min_seconds`, no source frame shown by two layers, a licence record on every music file and sound (`lungecuta licence KEY --name --source --checked`), the same sound not twice in a row; the report lists the rules not checked yet |
| `lungecuta scope COMP --at P` / `lungecuta scope --media KEY --at f:N` | waveform, RGB parade and vectorscope images, plus the numbers defined below: one PNG with the luma waveform and the vectorscope above and the parade below, from the frame `lungecuta still` gives at the same size |
| `lungecuta audio-check COMP [--from P --to P]` | integrated and per-section loudness, true peak, spectrogram image, cue alignment. Written so far: every cut's offset in ms to the nearest beat (and whether it is a downbeat) and to the nearest onset of the mix (SuperFlux), the onsets in the span, the mix's RMS per half beat with its slope in dB per beat ([07-audio.md](07-audio.md#analysis-and-checks)) |
| `lungecuta render COMP --preset P` | the deliverable, with `FILE.manifest.json` (comp and media hashes, colour settings, versions); in its first form Phase 0's part of the comp format: one picture track of cuts at constant speed, music mixed at unity gain, presets `master-prores-hq` and `web-h264` |
| `lungecuta verify FILE --comp COMP --preset P` | post-render checks: codec, profile, pixel format, size, rate, frame count, tags, audio; the frames either side of every cut against the engine's reference render (bias per channel within 1 code, *proposed*, PSNR, and each frame closer to its own reference than to the one across the cut); loudness within 0.2 LU of the preset's target and true peak at or under its ceiling ([08-deliver.md](08-deliver.md#verification)) |
| `lungecuta flash FILE` | WCAG 2.2's general flash threshold on every frame of a render, in the display's luminance (Lint, below); exits 1 when it fails. Red flashes are not checked |
| `lungecuta op OP COMP ...` | an edit operation (move, trim, slip, slide, roll, ripple delete, split, snap to beats, place, style, keys, remap, grade, looks, masks, transitions, sounds on events) applied by the same engine function the UI calls, values kept in their units, the comp written hash-checked, changes printed in editing terms ([06-editing.md](06-editing.md#edit-operations)) |
| `lungecuta changes` | what the UI wrote since the agent last asked, from the server's journal ([01-architecture.md](01-architecture.md#files-edits-and-concurrency)); `--peek` leaves the agent's place where it is |
| `lungecuta serve` | the editor for the project in the browser ([01-architecture.md](01-architecture.md#the-editor-as-first-written)) |
| `lungecuta diff COMP [REV1] [REV2]` | what changed between two revisions, in editing terms ("shot3 out point moved b:68 to b:67+0.5"); a revision is a git revision, a comp file's path or `working`, by default HEAD against working |

### Conventions for every command

- **Finding the project:** `--project DIR`, or else the nearest `project.json` found walking
  up from the current directory, the way git finds a repository. None found is an error.
- **Output:** images go to `--out PATH`, or else to `<project>/cache/lungecuta/<command>/` with a
  name built from the media key or comp, the time and the size (`C0001_f352_960x540.png`);
  the path is printed. Numbers are
  printed to stdout as one JSON object, so an agent can parse them.
- **Writing:** a command that changes project files (`lungecuta import`, `lungecuta op`, `lungecuta relink`)
  writes each one atomically and only if its content hash is still the one the command
  read, the same rule the UI follows. Before editing a file by hand, the agent runs
  `lungecuta changes`, so it does not overwrite something the person just did.
- **Code values** in printed numbers are 10-bit output codes computed from the float
  frame before any quantization; images are 8-bit and for looking at only.
- **Schemas:** `lungecuta schema --write` regenerates `schema/*.json` from the pydantic models; a
  test fails when the committed files differ. In Phase 0 the schema covers `project.json`,
  media records and `roots.local.json`; comps, styles and grades follow in M0 and M2.

### Working in a project with Claude Code or Codex

An agent opened in a project directory reads its instructions from a file there: Codex
from `AGENTS.md`, Claude Code from `CLAUDE.md`, which can import another file with
`@AGENTS.md`. `lungecuta init` writes both, a `.mcp.json` that starts `lungecuta mcp` for Claude
Code and a `.codex/config.toml` that starts it for Codex (each keeping any other server
it lists; Claude Code asks once before starting a project's server, and Codex reads a
project's own config only once the person has trusted the project; both checked with
Claude Code and Codex 0.142.5 on 2026-10-13: the server starts in the project directory
and finds the project there), so the agent arrives knowing the project's files, the
time literals, the steps of every edit (`lungecuta changes` first, `lungecuta op` where it can, look
with `lungecuta still`, `lungecuta sheet` and `lungecuta scope`, check with `lungecuta audio-check` and `lungecuta lint`,
deliver with `lungecuta render`, `lungecuta verify` and `lungecuta flash`) and what it cannot judge. The
guide sits between two markers in `AGENTS.md`; `lungecuta guide --write` replaces it with the
installed version's and keeps whatever the person wrote outside them, and a test fails
when the guide names a command or an `lungecuta op` operation the CLI does not have
(`src/lungecuta/guide.py`, `tests/test_guide.py`).

### Scope numbers

Computed on the display-encoded output of the project's display and view, which is what
the scopes of a grading application show:

- luma `Y' = 0.2126 R' + 0.7152 G' + 0.0722 B'` (BT.709, on the encoded values, which is
  the definition of video luma), reported as the 1st, 50th and 99th percentiles in 10-bit
  codes;
- clipped fraction: share of pixels with any channel at or below 0, or at or above 1.0,
  before quantization, printed as `any`, with `low` (at or below 0) and `high` (at or
  above 1) separately, since a pixel can be both. Through the ACES 2.0 view no value of the
  S-Log3 sample's frame 352 is below 0, and 9.9 % of its pixels are exactly 0 in some
  channel (its dark noise), so they all count as `low`; at 540 lines it is 4.9 %, since the
  downscale averages the noise first, so clipped fractions are compared at one size
  (`bench/sheet_scope_cost.py`);
- per-channel minimum, maximum and median (printed with the 1st and 99th percentiles, as
  `lungecuta still` prints them);
- vectorscope: `Cb`, `Cr` from the BT.709 matrix (`Cb = (B' - Y') / 1.8556`,
  `Cr = (R' - Y') / 1.5748`), reported as the median chroma magnitude
  `sqrt(Cb^2 + Cr^2)` and its 99th percentile, times 1023 like the other codes.

The images, for looking at: waveforms 512 rows high, code 0 at the bottom and 1023 at the
top, a line every 128 codes, values outside drawn on the edge rows; the luma waveform
1024 columns of the frame wide, each parade channel 512; the vectorscope 512 square with
`Cb` to the right and `Cr` up, boxes at the 75 % and 100 % colour bars (positions computed
from the same matrix); brightness is the pixel count on a log scale. These sizes are
layout choices, not measurements.

No skin-tone reference line is drawn or measured until its angle comes from a cited source.

Every command that produces an image also prints the numbers behind it, because an
agent's reading of a picture is approximate and a number is not. "Highlights are
clipping" is a claim to check with `lungecuta scope`, not to make from a thumbnail.

## Lint: rules learned the hard way

Past projects arrived at these rules over several review rounds. The
ones a machine can check become `lungecuta lint` warnings, each with its threshold in the
project settings (`project.json`'s `lint`, `null` or `false` turning a rule off) so it can
be tuned or turned off. Flashes are checked on a render rather than a comp, since a
flash can come from any layer, a graphic or the cutting itself: `lungecuta flash FILE` decodes
every frame and applies WCAG 2.2's general flash threshold (success criterion 2.3.1 and its
definitions, https://www.w3.org/TR/WCAG22/, read 2026-10-13) to the luminance the display shows, CIE Y through the display's EOTF in OCIO.
A flash is two opposing changes of 0.1 in relative luminance, the darker under 0.8; the
file fails where more than three fall within a second over more than a quarter of a
10 degree field. Taking the frame as the screen, filling its height, makes that field
WCAG's 341 x 256 of 1024 x 768 at a third of the frame's height, and luminance is averaged
over cells of a tenth of a degree, the size of the fine patterns WCAG excepts (*proposed*:
the definition gives no number for a video frame; `src/lungecuta/flash.py`). Red flashes
are not checked: WCAG 2.2 takes their working definition from ISO 9241-391, which leaves
which R, G and B it means to a standard not read. ITU-R BT.1702, the broadcast rule, could
not be read (its site and Ofcom's refuse automated downloads), so WCAG is the standard
until the user chooses otherwise.

| rule | check |
| --- | --- |
| text cards are readable | a text layer is on screen at least 0.6 s |
| flashes are not strobing | no more than three general flashes a second over a quarter of a 10 degree field (`lungecuta flash` on the render) |
| montage shots do not repeat | no source range is used twice in a comp |
| SFX vary | the same sound is not on two consecutive sound layers (`lint.sfx_vary`) |
| no dead gap before the hit | no silence or black between the crescendo and the hit marker |
| nothing black by accident | no comp frame with nothing on screen (`lint.no_black`): a gap between cuts, or the frame before a first cut on a beat that falls a few milliseconds after the comp's start |
| on-screen claims have sources | a text layer containing a number carries a `source` note |
| fonts are licensed | every font used is in the font registry with a licence that allows it (SF Pro was dropped for this reason) |
| music is cleared | every music file carries a licence record (CC0 checked on the page, not the search filter) |

## The review loop

The order past projects arrived at, made into commands:

1. `lungecuta validate`
2. `lungecuta sheet` at one frame per beat over the changed range, and `lungecuta still` on the frames
   that matter (hits, titles, faces).
3. `lungecuta audio-check` whenever sound changed.
4. `lungecuta render`, then `lungecuta verify`.
5. Commit. A version the person has seen is never overwritten; each feedback round is a
   new commit.

## What the agent cannot judge

It cannot watch playback in real time and it cannot hear. It can say that a cut lands
on a beat, that a ramp needs 4.2 s of source, that a title is on screen for 0.4 s and
needs 0.6 s to be read, that the mix peaks at -0.3 dBTP. It cannot say whether a ramp
feels right or a drop hits hard. Those calls are the person's, and the UI is built so
that making them is fast; the agent's job is to make everything around them
automatic and to carry an adjustment the person made in one place to the others.

## Sandboxing

A project can contain code: property expressions and code-driven graphics. Opening a
project must never run that code with the user's privileges.

- **Expressions** run in an embedded JavaScript engine with no file, network or process
  access and a fixed API ([03-project-format.md](03-project-format.md#properties-keyframes-easing-expressions)).
- **Graphics code** runs in the graphics runtime's browser sandbox, served only the
  project's own `graphics/` and asset files, with no network access unless the project
  declares an allowed origin.
- **Project files never contain Python.** New effects are added to the engine as code
  with tests; a style or a project can only refer to them by name.
- A project from elsewhere opens with its code listed for review, the way Blender holds
  back auto-run scripts.
