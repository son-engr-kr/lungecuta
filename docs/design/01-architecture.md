# Architecture

## Components

| component | language | job |
| --- | --- | --- |
| `lungecuta.engine` | Python (numpy, OpenColorIO, PyTorch where measured) | decode, colour, composite, effects, time remap, audio mix, encode |
| `lungecuta.server` | Python (FastAPI) | serves the UI, answers frame and scope requests, pushes file and render events |
| UI | vanilla JS, Canvas | viewer, layer timeline, graph editor, colour page, audio, deliver; runs in the same pinned Chromium build that captures graphics (D8) |
| graphics runtime | web layers in pinned Chromium (tier 1), Skia in the engine (tier 2); see [05-motion-graphics.md](05-motion-graphics.md) | renders code-driven and keyframed graphics at frame N, size WxH, with alpha |
| expression runtime | embedded sandboxed JS engine (QuickJS is the first candidate), from M9 | evaluates property expressions with no file or network access |
| `lc` CLI | Python | everything the agent needs: validate, probe, detect, beats, stills, sheets, scopes, render, checks |

The stack is Pickapicka's (FastAPI plus a vanilla JS client, PyInstaller for the app),
because the server and packaging problems are solved there. Its render engine is a
different matter: Pickapicka is display-referred and 8-bit with no colour management
(D3), so what is reused is structure, and the colour operations come from OpenColorIO
(D2, D9).

## One frame

```
source file
  -> decode            decoder chosen per codec and verified at import; matrix and range explicit
  -> input transform   camera log -> DaVinci Wide Gamut                      (OpenColorIO)
  -> clip grade        node graph of OCIO grading transforms plus keys and windows
  -> time sample       source time from the layer's remap curve; flow when between frames
  -> geometry          transform, crop, track-lock, stabilization offsets
  -> composite         layers in stack order, in DaVinci Intermediate by default
                       adjustment layers apply to everything below them
  -> output transform  DWG -> display (Rec.1886 Rec.709 by default)          (OpenColorIO)
  -> top overlays      the run of display-referred layers at the top of the stack
  -> encode            ffmpeg, 10-bit or better, tags set on frames and checked afterwards
```

### Where display-referred layers enter

Titles, captured screens, web graphics and D-Log M clips (after DJI's LUT) are
display-referred: their code values are what should appear in the deliverable. The stack
is processed in order, and such a layer enters in one of two ways:

- **Top overlays.** The contiguous run of display-referred layers at the top of the stack,
  with no scene-referred layer or adjustment above them, is composited after the output
  transform on display-encoded values. Their colours come out exactly, and they blend the
  way design tools blend. This is the common case: titles over footage.
- **Everywhere else**, a display-referred layer is brought into the working space through
  the inverse of the output view, at its place in the stack, so that it composites in
  order and can be graded by an adjustment above it. Measured round trip
  (`bench/RESULTS.md`): median under 0.15 ten-bit codes, p99 up to 4.2, worst case about
  11 codes on the most saturated colours.

Both rules keep stack order; what differs is only the space a layer blends in, and that
follows from where it sits.

## Preview and final

The preview runs the same chain on a proxy and stops at the same output transform. What
differs is resolution and quality settings the person can see (optical flow can be set
to frame blend while scrubbing).

Parity is a property established by tests, not by construction:

- proxy against original, measured at import (the criterion is open; provisionally 2 codes
  in the file's own R'G'B' codes, [02-media.md](02-media.md#proxies));
- every effect defined in comp-relative units (a shake amplitude is a fraction of comp
  height, never pixels), so a 540p preview and a 2160p render show the same motion;
- a captured web layer against its live overlay in the editor, diffed;
- any faster implementation of an operation against the reference.

The engine's render is the truth. The editor draws web layers live over engine frames
while they are being edited, as an interaction preview; for top overlays that preview is
close to the render, and the capture diff above says how close.

## Playback

- Every clip gets an all-intra 10-bit proxy ([02-media.md](02-media.md#proxies)), so any
  frame is one seek: about 5 ms for the provisional lossless FFV1 4:4:4 through PyAV with
  the container kept open, against 140 to 170 ms for one ffmpeg process per seek
  (`bench/pyav_check.py`); PyAV decodes the samples identically to the ffmpeg CLI. Its size follows the framing: a 9:16 comp cut
  from 16:9 sources needs a 1080-line proxy, because a 960x540 proxy leaves a 304x540
  crop for a 1080x1920 viewer.
- The engine renders preview frames into a cache keyed by (comp, frame, content hash of
  every input of that frame, and the versions that decide its pixels: engine, OpenColorIO,
  the config, the clip's decoder, the Chromium build for graphics). Without the versions,
  an upgrade would keep serving frames the new code would not make. A final render writes
  the same versions into a manifest next to the file. Editing a keyframe invalidates only
  the frames it reaches; a timeline-wide grade invalidates everything, which is why the
  full-chain cost per preview frame has to be measured before the editor is built
  ([10-roadmap.md](10-roadmap.md)).
- How the cache stores frames, and so how big it gets, is decided in Phase 1. Uncompressed,
  a 960x540 8-bit RGB frame is 1.56 MB: 2.8 GB for 60 s at 30p and 8.4 GB for three
  minutes, on a machine with 24 GB of memory.
- Frames go to the browser as sRGB-encoded 8-bit RGB without chroma subsampling, and
  Chrome maps them to the display. The 10-bit proxy is for the engine's arithmetic; the
  screen is 8-bit either way.
- The browser plays the engine's audio mixdown with Web Audio, and the frame on screen is
  chosen from `AudioContext.getOutputTimestamp()` corrected by `outputLatency`, because
  `currentTime` alone ignores output latency (100 to 250 ms on Bluetooth). A frame that is
  not rendered yet is shown as missing, never replaced with a stale one.

## Compute plan

**Year one runs OpenColorIO on the CPU, with threads, for preview and final.** It is the
reference implementation, so parity with it is exact, and it is fast enough: the ACES 2.0
output view costs 144 ms per 4K frame and 10 ms per 540p frame on 15 threads (OCIO
releases the GIL). The single-thread figure (1.7 s per 4K frame) led an earlier version
of this design to require the GPU; it does not.

Other work goes to the GPU only when it is measured to need it:

- **PyTorch on MPS** for frame interpolation (RIFE), optical flow and heavy pixel
  operations (blur, resampling), checked against a numpy reference for numerical
  agreement, not just for running.
- **Not a substitute for the output transform:** a baked 65^3 LUT sampled trilinearly is
  wrong by more than one code on half of near-neutral colours (p99 5.8, max 77), so
  `grid_sample` on a baked view fails the colour criterion.
- **Out of year one:** hand-written Metal kernels, a CUDA extension, native modules.
  OCIO's generated Metal shader for the ACES 2.0 view needs textures, which
  `torch.mps.compile_shader` does not take, so running it on the GPU is its own project.

Frames move between CPU and GPU as little as possible; a round trip between two effects
can cost more than either effect.

## Files, edits and concurrency

- A project is a directory of JSON files ([03-project-format.md](03-project-format.md)).
  The server watches it; a change made by the agent appears in the UI without a reload.
- The server keeps the content hash of every file it last read. A UI write states the
  hash it was based on; if the file changed underneath (the agent edited it), the write
  is refused and the UI shows both versions. Nothing in the files themselves has to be
  maintained for this, so the agent edits them like any other JSON. `lc` commands that
  write files follow the same rule.
- **That protects the agent's edits from the UI, not the other way round.** An agent that
  reads a file, works, and writes it back by hand can overwrite a UI change made in
  between, and nothing would notice. So every UI write also goes into a journal in the
  cache (file, hash before and after, the change in editing terms), `lc changes` shows the
  agent what the person did since it last asked, and an overwritten UI change can be
  recovered from the journal. The journal is also how principle 1 is kept: the agent sees
  exactly what the person adjusted, without waiting for a commit.
- **Undo and redo** in the UI walk the same journal, file by file, so an undo is itself a
  write the agent sees in `lc changes`.
- The project directory is meant to be a git repository. Every review round is a commit,
  so a version someone has seen is never overwritten (the rule past projects arrived at
  by hand).

## The editor as first written

`lc serve` (`src/lungecuta/server.py`, `preview.py`, `web/`) is M3's first form:

- **Preview frames** come from `Preview`: each clip's proxy read by PyAV with its
  container kept open, the same chain as a render (H.273, input transform, transform and
  composite in the working space, output view), then from the display's encoding to
  sRGB for Chrome, sent as RGBA over a WebSocket and cached by the comp's version (its
  file's hash and the size and time of the styles and LUTs it uses, so editing a linked
  style refreshes every placement) and the frame. The preview size is the comp divided by the smallest whole number that
  brings it to `proxy_height` or under.
- **The UI** (vanilla JS, one canvas each for viewer and timeline): the timeline draws
  layers in stack order (the top row is the top of the stack), the ruler, the beat grid
  (beats as ticks, downbeats as lines with bar numbers) and markers; dragging a layer
  moves it and dragging an edge trims it, both snapped to beats (Alt for whole frames)
  and sent as `lc op`'s own operations with the comp hash the UI showed; S splits at the
  playhead, Delete ripples, Shift with the arrows steps by beats, Cmd+Z and Shift+Cmd+Z
  undo and redo. A frame not rendered yet shows as missing. Keys show as diamonds on
  their layer's row (white for the transform, blue for remap).
- **The inspector** shows the selected footage layer's position, scale and opacity at the
  playhead, from the engine's own evaluation (`/api/comp/C/layer/L/curves`, every frame's
  values): the diamond button adds or deletes a key there, the menu sets the ease of the
  key under the playhead, and typing a value sets a key when the property is animated and
  the constant when it is not, as in After Effects. Its time section enables time
  remapping, keys the source shown at the playhead, and places a style on the nearest
  beat or takes it off; its grade section edits the primary grade in working-space codes
  (`offset`, `contrast`, `pivot`, `gamma`, `saturation`), every change one `lc op`
  operation (`key`, `unkey`, `set`, `remap`, `style`, `unstyle`, `grade`).
- **The graph editor** under the timeline draws the selected layer's speed (the source
  time to the next shown frame, per comp frame) or its source time against comp time,
  with the remap keys on it; in the source view a key is dragged up or down to change its
  source time, in whole source frames unless Alt is held. The speed view is a view of
  the remap, not a second store.
- **Scopes** (the Scopes button): the waveform, vectorscope and parade `lc scope` draws,
  with its luma numbers, of the frame on screen once the playhead rests, computed by the
  engine on the preview frame in the project's display encoding, so on the proxy and
  without the graphics the editor draws live; `lc scope` on the original is the number to
  quote.
- **Playback** plays the comp's mixdown (`render.mix`, as WAV) through Web Audio, and the
  frame on screen is the one under `AudioContext.getOutputTimestamp().contextTime`, the
  time now reaching the output; frames ahead are asked for as it plays.
- **Graphics layers** are drawn live in the page, each in an iframe from a second origin
  (another port) that serves only the project's `graphics/` with a policy allowing
  nothing but that origin; a wrapper page there takes the editor's `postMessage` with the
  layer time and calls the page's `renderAt`.
- **Writes** from the UI go through `lc op`'s functions with the hash the UI was based
  on and into the journal (`cache/journal.jsonl`) with their content before and after;
  undo and redo walk it and refuse to overwrite a change made since; `lc changes` shows
  the agent the entries it has not seen. The server polls the project's JSON files four
  times a second from a snapshot taken at start-up and tells the UI when one changes
  underneath it, which is how the agent's edits appear.

Checked headless on a copy of the Phase 0 check comp (`bench/editor_smoke.py`,
2026-10-13): frames drawn, two beat steps landing on b:1, two seconds of playback
advancing 72 to 73 frames, a drag of one beat written as `b start moved b:9 to b:10` and
journalled, no page errors. The first version lost track of a frame asked for just before
an edit (its reply came under the new hash) and stopped asking; requests are now matched
to replies by id.

## Process model and libraries

- One server process per open project; renders run in worker processes so a final render
  does not block preview requests.
- The graphics runtime and the expression runtime are separate sandboxes, reached by
  message.
- Media I/O uses ffmpeg as a subprocess for sequential work (proxy builds, final reads,
  encodes). In-process random access (`lc still` at final quality reads originals at
  arbitrary frames) goes through PyAV, which bundles an LGPL FFmpeg, only once its decode
  has passed a framemd5 check against the ffmpeg CLI. It has, on the S-Log3 sample and its
  FFV1 proxy (705 of 705 frames each, `bench/pyav_check.py`, 2026-10-13); Phase 0's stills
  still use the CLI with accurate seeking ([02-media.md](02-media.md#decode)), and the
  check is run on each new codec before PyAV reads it.
- Licence: MIT, like Pickapicka, with its rules for model weights (D10). The Homebrew
  ffmpeg is GPL (libx264, libx265); calling it as a subprocess does not affect
  lungecuta's licence. An app bundle would ship an LGPL ffmpeg, which loses x264, so the
  VideoToolbox H.264 encoder has to be measured against x264 before a bundle is offered.
