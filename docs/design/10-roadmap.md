# Roadmap

Order follows risk: the things most likely to sink the project are measured before
anything expensive is built. Every exit criterion is a number or a comparison that a
broken implementation cannot pass. Thresholds marked *proposed* are design targets for
the user to confirm, not measurements.

## Phase 0: the baseline (days)

Agent tools on top of Resolve free, no editor:

- `lc import`, `lc detect`, `lc still`, `lc sheet`, `lc scope` on CPU OpenColorIO, so the
  agent can see footage and frames with correct colour;
- the agent writes a cut (beats, cut points, constant speed segments, markers) as FCPXML,
  imported into Resolve free through File > Import > Timeline. Blackmagic's 21.1 release
  notes say "Advanced scripting now requires DaVinci Resolve Studio", and secondary
  sources report that Python scripting moved to Studio (`docs/research/resolve-notes.md`),
  so file import is the only route that does not depend on Resolve's scripting policy;
- graphics rendered by the existing web-page renderer to ProRes 4444 with alpha.

This is useful on its own, and it is the honest baseline: M3 has to beat it, not manual
Resolve. Whether FCPXML variable speed ramps arrive in Resolve as editable curves (the
manual says variable speed is converted to one keyframe per frame; "About Speed Effects",
p. 485 of the manual installed with 21.0.4) is tested here, along with three things the
baseline depends on and nobody has checked:

- **in points land on the intended source frame.** The originals carry a start timecode
  (00:00:21:15 on the S-Log3 sample) and FCPXML addresses clips by it, while lungecuta
  counts source time from the first frame ([02-media.md](02-media.md#rates-and-timecode));
  a few cuts are checked frame by frame in Resolve's viewer against `lc still`;
- **cuts land on frames by the stated rule**
  ([03-project-format.md](03-project-format.md#from-time-to-frames-and-samples)), and the
  music the cut was timed against is the PCM WAV the beats were found in
  ([02-media.md](02-media.md#music-and-beats));
- **what a graphic's colours become in Resolve.** A card of known colour patches, rendered
  to ProRes 4444 by the same renderer, is put over footage in a DWG/DI timeline, exported
  and read back: if Resolve sends it through the output tone mapping, titles change colour
  in the baseline, which is the problem lungecuta's top overlays solve
  ([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)).

The commands for all of this exist (`lc fcpxml`, `lc still`, `lc patch-card`, `lc patches`),
and the checks themselves are a procedure for the person, since they need Resolve's GUI:
[docs/research/phase0-resolve-checks.md](../research/phase0-resolve-checks.md).

**The time is recorded by stage** (choosing material, cutting, grading, graphics, audio,
rendering, each review round), with the material described (length, number of shots,
number of graphics), so that M3 is compared on comparable material. One timed edit is a
noisy baseline that the person also gets faster at, so Phase 0-style edits made while M0
to M2 are built are logged the same way, and M3 is compared against all of them.
*(proposed)*

Phase 0's media code (time literals, detection, import, proxies, stills) is production
code, written to M0's standards, and M0 continues it; Phase 0 does not claim M0's exit
criteria. Its FCPXML exporter is a tool for the baseline and may be discarded later.

**Exit:** one real music edit made this way end to end, with its time recorded by stage.

## Phase 1: the spike (two to three weeks)

A throwaway prototype of the preview loop, to measure the risks the editor depends on.
Each item is a kill criterion:

| risk | measurement | criterion |
| --- | --- | --- |
| interaction latency | a grade or keyframe change to updated pixels in the viewer, full chain (proxy decode, input transform, grade, composite, output view, encode, transport, draw) | p95 at most 150 ms at 540p (*proposed*) |
| cache refill | a timeline-wide change on a 60 s comp: time until every preview frame is rendered | at least real time (*proposed*) |
| proxy parity | proxy against downscaled original | open: 2 DI codes is unreachable by construction; provisionally 2 R'G'B' codes and bias under 0.5 ([02-media.md](02-media.md#proxies)) |
| colour against Resolve | a chart frame including saturated patches, through Resolve's CST and through the engine | median within 1 code, p99 within 2 |
| data levels and matrix | lens-cap and chart from each camera body, raw codes read directly | range and matrix settled per body |
| on-screen colour | a screenshot of the viewer in the pinned Chromium against the frame the engine sent | within 1 code (*proposed*) |
| graphics parity | one HTML title captured by the pool against the same title as a live overlay | p99 within 2 codes (*proposed*) |

If latency or refill cannot be met with CPU OpenColorIO on threads, the options are a
smaller preview size or GPU work for named stages, decided by the measurement; if neither
gets there, the editor is not built and Phase 0 remains the tool.

**Measured 2026-10-13** (`bench/spike/`, `bench/RESULTS.md`, "Phase 1 spike"), on CPU
OpenColorIO with the provisional FFV1 proxy read by PyAV:

- interaction latency: p95 51 ms for a grade on a parked frame and 62 ms for a new
  two-layer frame each time: **met**; with an ffmpeg process per frame instead of PyAV,
  347 ms;
- cache refill: 1.42 times real time one frame at a time, 2.30 with 15 frames in
  parallel, for a one-layer 60 s comp: **met**;
- on-screen colour: 0 codes between the frame sent and the viewer's screenshot: **met**;
- graphics parity: p99 1 code, max 1: **met**;
- proxy parity: the criterion is open (lossless FFV1 4:4:4 is at the rounding bound in
  R'G'B' codes, 3.9 DI codes at p99);
- colour against Resolve and data levels per body: **not measured**, they need a chart
  and lens-cap shots from each body, in Resolve and from the cameras.

## M0: foundations

Project schema and validation, the tick time model with unit literals and the rules for
source time and for frames and samples, `lc import` with the per-codec decoder check,
rates and timecode, media records as files under named roots with `lc relink`, proxies
with their own decode records, beat maps with downbeats, the comp grid from several music
layers, and the re-analysis rule.

**Exit:** a real shoot folder imports, and every clip's decided colour space matches a
hand-labelled list of that shoot (clips the list marks unknown stay undecided); time
literal round trips are covered by tests across every rate in
[03-project-format.md](03-project-format.md#the-unit).

## M1: one frame, correct colour

Stills through the full colour chain on CPU OpenColorIO: decode with explicit matrix and
range, input transform, OCIO grading transforms, output view.

**Exit:** the Phase 1 colour and parity criteria, now on the production code: chart
against Resolve (median 1, p99 2 codes, saturated patches included) and proxy against
original (the criterion the user settles from the Phase 0 measurement).

## M2: a timeline renders

Cuts, remap and constant speed (`nearest`, `blend`), layer timing as in After Effects
with the edit operations behind `lc op`, transform keyframes with easing, a minimal style
instance, audio pass-through mix, final encode with tags set on frames and a version
manifest.

**Exit:** a past deliverable's cut, rebuilt as a project, matches that deliverable frame by
frame at every cut, and `lc verify` passes its tag, bias and loudness checks.

## M3: the editor, and the go/no-go

Viewer, layer timeline, beat grid, remap and speed graphs, keyframe editing, playback
against the audio output clock, file watching with the agent editing alongside, the UI
write journal with `lc changes`, and undo and redo on it.

**Exit, and the decision:** two new edits (not re-cuts of earlier ones), each made in
lungecuta and timed against the Phase 0 workflow on comparable material, each finished to
a quality the user accepts for delivery, with the Phase 1 latency criterion holding on
real projects. If lungecuta is not faster, stop or re-scope before M4.

## M4: the colour page

Node graphs of OCIO grading transforms with dynamic properties, keys (qualifier, windows
following tracks), LUTs with declared spaces, scopes computed by the engine and drawn by
the UI, looks as linked styles; then shot match and film look.

**Exit:** one real edit graded entirely in lungecuta; on chart frames, waveform and
vectorscope readings agree with Resolve's within 2 codes (*proposed*).

## M5: motion graphics, transitions, tracking

The web-layer tier ([05-motion-graphics.md](05-motion-graphics.md)): built-in text and
shape layers, code-driven layers, precomps, masks, mattes, blend modes; the transition
catalogue ([06-editing.md](06-editing.md#transitions)); the point tracker and track-lock.

**Exit:** one past promo (D4) rebuilt, with every catalogue item it used
reproduced, and its review loop timed against the original's.

## M6: flow and motion blur

RIFE retiming with flow estimated on a display-rendered copy, vector motion blur, both
costs measured per frame.

**Exit:** side-by-side against Resolve free's Optical Flow on a set of the user's ramps
that includes fast motion and occlusion, judged by the user.

## M7: audio

Tracks, gain automation, fades, ducking, the effects past edits used, normalization then
a true-peak limiter ([07-audio.md](07-audio.md)).

**Exit:** a past promo's audio rebuilt, passing the same loudness checks, with cut-to-onset
offsets reported by `lc audio-check`.

## M8: styles from words, lint, diff

Style library UI, linked instances with overrides, the agent writing a style from a
description and proving it with a sheet; `lc lint`; `lc diff`.

**Exit:** three styles described in words, created, adjusted by the user and applied to
several places each in one edit.

## M9: expressions, privacy, generated sound

The sandboxed expression runtime; face and plate anonymization with the final-frame audit;
synth voices and TTS.

**Exit:** a piece with people in public rendered with no face found by `lc face-audit`
outside the allowed list, its sheet checked by the user.

## M10: packaging

App build and self-update following Pickapicka's PyInstaller setup, with the VideoToolbox
H.264 encoder measured against x264 first ([08-deliver.md](08-deliver.md#licences)).

## Not planned for year one

Hand-written Metal kernels, a CUDA extension, native modules; the Skia graphics tier;
parallel and layer-mixer nodes; HDR delivery.
