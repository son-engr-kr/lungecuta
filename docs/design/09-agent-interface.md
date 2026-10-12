# Agent interface

The agent edits the project's JSON files directly; nothing about that needs a special
protocol. What it needs from lungecuta is **eyes and checks**: ways to see a frame, a
stretch of timeline, a scope or a loudness curve, and ways to find out whether the
project still means what it should. Everything here is a CLI command first. An MCP
server, if one is added, is a thin wrapper over the same commands.

## Commands

| command | output |
| --- | --- |
| `lc init DIR` | a new project directory: `project.json` with the colour defaults of [03-project-format.md](03-project-format.md#projectjson), `proxy_height` 540, `media` `{}`, `comps` `[]`, `deliver` `[]`; empty `comps/`, `grades/`, `styles/`, `graphics/`, `luts/`, `cache/`; refuses a non-empty directory. The `.lc` suffix is a convention, not required |
| `lc import FILES...` | media records: probe, colour decision with evidence, decoder check, proxy, beat map for music |
| `lc detect FILE` | the colour-space decision and every piece of evidence considered, without importing |
| `lc validate` | schema and meaning checks ([03-project-format.md](03-project-format.md#validation)) |
| `lc still COMP --at T` | PNG of the final-quality frame of a comp at a time literal, at a chosen size |
| `lc still --media KEY --at f:N` | the same for one imported clip through its input transform and the project's view, with no comp needed (Phase 0's form) |
| `lc sheet COMP --every b:1` | contact sheet: frames at an interval, each labelled with time, beat and layer names |
| `lc sheet COMP --fps 10` | timeline sheet, the dense version that catches timing bugs stills miss |
| `lc sheet --media KEY --every f:30` | contact sheet of one clip, frames labelled with frame number and seconds (Phase 0's form) |
| `lc framing COMP LAYER` | the source frame with start and end crop windows and a 0.1 grid |
| `lc face-audit FILE` | every face the detector still finds in a render, as a crop sheet with scores |
| `lc lint` | taste and rights rules (below) as warnings |
| `lc scope COMP --at T` / `lc scope --media KEY --at f:N` | waveform, RGB parade and vectorscope images, plus the numbers defined below |
| `lc audio-check COMP` | integrated and per-section loudness, true peak, spectrogram image, cue alignment |
| `lc render COMP --preset P` | the deliverable |
| `lc verify FILE --preset P` | post-render checks: tags, frame count, duration, loudness, picture bias per channel, cut times ([08-deliver.md](08-deliver.md#verification)) |
| `lc diff REV1 REV2` | what changed between two revisions, in editing terms ("shot3 out point moved b:68 to b:67+0.5") |

### Conventions for every command

- **Finding the project:** `--project DIR`, or else the nearest `project.json` found walking
  up from the current directory, the way git finds a repository. None found is an error.
- **Output:** images go to `--out PATH`, or else to `<project>/cache/lc/<command>/` with a
  name built from the media key or comp and the time; the path is printed. Numbers are
  printed to stdout as one JSON object, so an agent can parse them.
- **Code values** in printed numbers are 10-bit output codes computed from the float
  frame before any quantization; images are 8-bit and for looking at only.
- **Schemas:** `lc schema --write` regenerates `schema/*.json` from the pydantic models; a
  test fails when the committed files differ. In Phase 0 the schema covers `project.json`
  and media records; comps, styles and grades follow in M0 and M2.

### Scope numbers

Computed on the display-encoded output of the project's display and view, which is what
the scopes of a grading application show:

- luma `Y' = 0.2126 R' + 0.7152 G' + 0.0722 B'` (BT.709, on the encoded values, which is
  the definition of video luma), reported as the 1st, 50th and 99th percentiles in 10-bit
  codes;
- clipped fraction: share of pixels with any channel at or below 0, or at or above 1.0,
  before quantization;
- per-channel minimum, maximum and median;
- vectorscope: `Cb`, `Cr` from the BT.709 matrix, reported as the median chroma magnitude
  and the 99th percentile.

No skin-tone reference line is drawn or measured until its angle comes from a cited source.

Every command that produces an image also prints the numbers behind it, because an
agent's reading of a picture is approximate and a number is not. "Highlights are
clipping" is a claim to check with `lc scope`, not to make from a thumbnail.

## Lint: rules learned the hard way

Past projects arrived at these rules over several review rounds. The
ones a machine can check become `lc lint` warnings, each with its threshold in the
project settings so it can be tuned or turned off:

| rule | check |
| --- | --- |
| text cards are readable | a text layer is on screen at least 0.6 s |
| flashes are not strobing | at most about 3 bright flashes per second |
| montage shots do not repeat | no source range is used twice in a comp |
| SFX vary | the same sample is not on consecutive cuts |
| no dead gap before the hit | no silence or black between the crescendo and the hit marker |
| on-screen claims have sources | a text layer containing a number carries a `source` note |
| fonts are licensed | every font used is in the font registry with a licence that allows it (SF Pro was dropped for this reason) |
| music is cleared | every music file carries a licence record (CC0 checked on the page, not the search filter) |

## The review loop

The order past projects arrived at, made into commands:

1. `lc validate`
2. `lc sheet` at one frame per beat over the changed range, and `lc still` on the frames
   that matter (hits, titles, faces).
3. `lc audio-check` whenever sound changed.
4. `lc render`, then `lc verify`.
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
