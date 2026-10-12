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

- proxy against original, compared in DaVinci Intermediate after decode, within 2 codes;
- every effect defined in comp-relative units (a shake amplitude is a fraction of comp
  height, never pixels), so a 540p preview and a 2160p render show the same motion;
- a captured web layer against its live overlay in the editor, diffed;
- any faster implementation of an operation against the reference.

The engine's render is the truth. The editor draws web layers live over engine frames
while they are being edited, as an interaction preview; for top overlays that preview is
close to the render, and the capture diff above says how close.

## Playback

- Every clip gets a ProRes Proxy 10-bit file, all-intra, so any frame is a cheap seek
  (22 ms including an ffmpeg process start). Its size follows the framing: a 9:16 comp cut
  from 16:9 sources needs a 1080-line proxy, because a 960x540 proxy leaves a 304x540
  crop for a 1080x1920 viewer.
- The engine renders preview frames into a cache keyed by (comp, frame, content hash of
  every input of that frame). Editing a keyframe invalidates only the frames it reaches;
  a timeline-wide grade invalidates everything, which is why the full-chain cost per
  preview frame has to be measured before the editor is built ([10-roadmap.md](10-roadmap.md)).
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
  maintained for this, so the agent edits them like any other JSON.
- The project directory is meant to be a git repository. Every review round is a commit,
  so a version someone has seen is never overwritten (the rule past projects arrived at
  by hand).

## Process model and libraries

- One server process per open project; renders run in worker processes so a final render
  does not block preview requests.
- The graphics runtime and the expression runtime are separate sandboxes, reached by
  message.
- Media I/O uses ffmpeg as a subprocess for sequential work (proxy builds, final reads,
  encodes). In-process random access (`lc still` at final quality reads originals at
  arbitrary frames) goes through PyAV, which bundles an LGPL FFmpeg, only once its decode
  has passed a framemd5 check against the ffmpeg CLI; until then, and in Phase 0, stills
  use the CLI with accurate seeking ([02-media.md](02-media.md#decode)).
- Licence: proposed MIT, like Pickapicka, pending the user's decision. The Homebrew
  ffmpeg is GPL (libx264, libx265); calling it as a subprocess does not affect
  lungecuta's licence. An app bundle would ship an LGPL ffmpeg, which loses x264, so the
  VideoToolbox H.264 encoder has to be measured against x264 before a bundle is offered.
