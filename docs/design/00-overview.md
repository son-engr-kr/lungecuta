# Overview

lungecuta is a video editor for short, music-driven pieces shot in log, built so that an
AI agent and a person work on the same project. The agent writes the project as text
files; the person scrubs, drags keyframes and grades in a browser UI. Both see the same
frames, because one engine renders the preview and the final.

The feel it aims for is DaVinci Resolve's colour page and pipeline discipline joined to
After Effects' layer and keyframe model:

| taken from Resolve | taken from After Effects |
| --- | --- |
| input / working / output colour management | compositions, precomps, layers |
| node-based grade, scopes | keyframes, graph editor (value and speed), easing |
| retime curve, optical-flow retiming | time remap, motion blur |
| proxies, render cache | text and shape layers, masks, track mattes, blend modes |
| deliver presets with correct tags | expressions |

## Scope

- Timelines up to about three minutes; the target is 15 to 90 seconds.
- Output up to UHD (3840x2160, 2160x3840 vertical) at up to 60p. Sources up to 120p,
  used as slow motion.
- Sources: Sony S-Log3, S-Log2 and HLG; DJI D-Log, D-Log M and D-Log 2 (Osmo Pocket 4P);
  Apple Log; Rec.709 material (phones, screen recordings); stills and graphics. HLG and
  D-Log 2 have no defined input transform yet and import as undecided
  ([04-color.md](04-color.md#input-camera-spaces)).
- macOS on Apple Silicon first. The GPU layer goes through PyTorch so that an NVIDIA
  build stays possible, but nothing is promised for it.

## Non-goals

- Real-time 4K playback with every effect applied. Preview is 540p from a cache; the
  final render is offline.
- Multicam, collaborative editing, interlaced media, broadcast packages (IMF, DCP).
- RAW video formats (BRAW, R3D, ProRes RAW) in the first versions.
- Parity with Resolve's colour page or with the After Effects plug-in ecosystem.

## What "semi-pro" means here

Each item is something a test can check, not an adjective:

- **Colour transforms come from published math.** Camera, working and display spaces are
  taken from OpenColorIO's built-in studio config or from a vendor's published formula.
  A curve that has not been published (DJI D-Log M, at the time of writing) is applied
  from the vendor's own LUT and never reconstructed.
- **The reference is the implementation.** Year one renders colour with OpenColorIO's
  own CPU processors, on threads, for preview and final. Any faster path added later is
  compared against them on a dense, saturated pattern and may differ by at most one
  10-bit output code anywhere on it (*proposed*); a grey ramp is not a test.
- **No 8-bit stage in the footage path.** float32 inside the engine, 10-bit or better in
  proxies and outputs. Web graphics are 8-bit sRGB by nature and enter as
  display-referred material (D8).
- **Preview is held to the final.** A proxy frame is measured against the downscaled
  original at import, and a proxy that fails is rejected. The criterion is open: 2 codes in
  DaVinci Intermediate cannot be met by a proxy downscaled before the input transform, so
  the gate is provisionally 2 codes in the file's own R'G'B' codes
  ([02-media.md](02-media.md#proxies)).
- **Outputs carry correct tags.** Every deliverable is probed after encoding and its
  primaries, transfer, matrix and range are checked against what was intended.
- **Audio meets delivery loudness.** Integrated loudness and true peak are measured
  (EBU R128 via ffmpeg `ebur128`), not estimated.
- **Encoder settings are chosen by measurement**: PSNR against a lossless render of the
  same frames, the way the existing ingest scripts chose their bitrate.

## Principles

1. **The project is text.** The UI and the agent edit the same files. Every UI action is
   a change to a file, so the agent sees exactly what the person adjusted and can carry
   it to other places.
2. **One engine renders preview and final**, differing only in resolution and in quality
   settings the person chose. A preview that disagrees with the render cannot be graded
   against, and agreement is shown by tests with numeric limits, not assumed.
3. **Scene-referred colour, with OpenColorIO as the reference implementation.**
4. **Fail fast.** An unidentified colour space, a speed ramp that needs more source than
   the clip has, a style that refers to a missing parameter: each stops with a message
   that says what is missing. Nothing is guessed.
5. **Every speed-up keeps its slow reference** as an oracle that tests compare against.
6. **Measure the workload that exists.** Numbers that drive decisions live in `bench/`
   with the command that produced them.
7. **Project files never execute Python.** Code inside a project (expressions,
   code-driven graphics) runs in a sandbox, so opening someone else's project cannot
   touch the file system.

## Pages

The UI follows Resolve's page split, with the After Effects model living on the Edit page:

| page | what happens there |
| --- | --- |
| Media | import, colour-space detection, proxy build, beat analysis of music |
| Edit | composition viewer, layer timeline with beat grid, graph editor, styles |
| Color | node graph per clip and per timeline, wheels, curves, qualifiers, windows, scopes |
| Audio | tracks, gain and fades, ducking, loudness meter |
| Deliver | presets, render queue, post-render tag and loudness checks |

## Documents

| file | subject |
| --- | --- |
| [01-architecture.md](01-architecture.md) | components, data flow, preview and final, GPU plan |
| [02-media.md](02-media.md) | ingest, probe, colour-space detection, proxies, decode |
| [03-project-format.md](03-project-format.md) | time model, project files, layers, keyframes, styles |
| [04-color.md](04-color.md) | colour management, grading, scopes, output transforms |
| [05-motion-graphics.md](05-motion-graphics.md) | graphics runtime, text, shapes, code layers |
| [06-editing.md](06-editing.md) | cuts, speed ramps, transitions, tracking, stabilization |
| [07-audio.md](07-audio.md) | mixing, effects, loudness |
| [08-deliver.md](08-deliver.md) | codecs, bit depth, tags, presets |
| [09-agent-interface.md](09-agent-interface.md) | CLI, the agent's eyes, validation, sandboxing |
| [10-roadmap.md](10-roadmap.md) | milestones and the criteria for stopping |
| [decisions.md](decisions.md) | decision log with evidence |
