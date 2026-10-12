# Decisions

Each entry: what was decided, the evidence, what was rejected and why. Numbers come from
`bench/` unless stated. Research records are in `docs/research/`.

## D1: Python engine, FastAPI server, vanilla JS UI

**Decided.** Pickapicka's stack: Python with numpy, PyTorch and OpenColorIO for the
engine; FastAPI for the server; vanilla JS with Canvas for the UI; PyInstaller for the
app.

**Evidence.** The hard parts (colour transforms, LUTs, tracking, frame interpolation,
beat analysis) are Python libraries already. GPU work does not require leaving Python:
on this machine PyTorch MPS runs, `torch.mps.compile_shader` compiles and runs a
hand-written Metal kernel, and `grid_sample` on 5-D input (3D LUT sampling) runs on
MPS. 4K frames reach numpy at 52 fps through an ffmpeg pipe, faster than real time.

**Rejected.** Rust (the `jalara` workspace): the bottlenecks are GPU and hardware
encoding, not the host language, and the colour, ML and tracking code would have to be
rewritten. Swift and Metal natively: fastest on a Mac, but reuses nothing; worth it only
for real-time 4K playback with effects, which is a non-goal.

## D2: OpenColorIO 2.6 as the colour reference; DaVinci Wide Gamut as working space

**Decided.** Built-in `studio-config-v5.0.0_aces-v2.1_ocio-v2.6`; DaVinci Wide Gamut
working space, composited in DaVinci Intermediate by default; default display Rec.1886
Rec.709 with the ACES 2.0 SDR view. Grading operators are OCIO's own (D9). Year one runs
OCIO's CPU processors on threads for preview and final.

**Evidence** (`bench/ocio_check.py`, `bench/ocio_threads.py`). The config has every
camera space the user shoots except S-Log2, D-Log M and D-Log 2 (and has HLG only as a
display, not as an input space), and generates Metal shading
language for any processor. On 15 threads the ACES 2.0 output view costs 144 ms per 4K
frame and 10 ms per 540p frame (one thread: 1674 ms and 106 ms). DWG and DI are published
by Blackmagic (constants in `docs/research/color-sources.md`); DI puts 18 % grey at
0.336043.

*Amended after review (D9):* the first version concluded from the single-thread figure
that the output transform had to run on the GPU. It does not: OCIO releases the GIL and
scales on threads. A baked 65^3 LUT is also no substitute (`bench/ocio_lut_error.py`:
p99 5.8 codes, max 77, on near-neutral colours).

**Rejected.** Hand-rolled transforms with `colour-science` as the reference: it has no
ACES 2.0 output transform and no GPU shader generation, though it remains useful for
cross-checks and for S-Log2. Resolve's DaVinci tone mapping: not published, so it can
only be matched through a LUT the user exports from Resolve. OpenDRT (GPL-3.0) and AgX
(no licence) as defaults.

## D3: from Pickapicka, reuse structure, not colour maths

**Decided.** Reuse the edit schema pattern, presets, mask geometry and combination, the
window-and-padding render contract, the colour-match fit and the sharpening operator.
Grading operators come from OCIO instead of being rewritten (D9), and the `cv2.remap`
LUT path is replaced by OCIO's tetrahedral `FileTransform`.

**Evidence.** `docs/research/pickapicka-engine.md`: the engine is display-referred,
8-bit with several mid-pipeline 8-bit round trips, has no colour management, applies
Rec.709 luma weights to encoded values, relies on OpenCV's sRGB-assuming colour
conversions, treats 1.0 as diffuse white, and seeds grain per photo.

## D4: requirements come from what past edits built

**Decided.** The motion-graphics, audio and review requirements are the catalogue of
four past projects (kept locally under `docs/research/private/`, not published). M5's
exit criterion is that every item in it can be rebuilt.

Labels used for those projects across the design documents: **P1**, a 15 s 3D simulation
showreel (Three.js, 1920x1080, 60 fps); **P2**, a 30 s vertical promo of real footage
with Canvas2D and DOM graphics (1080x1920, 60 fps); **P3**, a 26 s promo and a 3 minute
guide built from screen captures of an app; **P4**, an unfinished promo combining
generated video and engine renders.

**What it settled.**
- Graphics need DOM, CSS, Canvas2D and WebGL together in one page, so canvas-only or
  vector-only engines are out (see D8).
- Every past page met a `renderAt(t)` pure-function contract; it is kept.
- Picture and sound kept cue times in two places and every version duplicated its
  builder; one timeline with shared cue definitions removes both.
- The review tools (framing, contact and timeline sheets, audio check with cut-to-onset
  offsets, face audit) become `lc` commands, and the taste rules a machine can check
  become `lc lint`.

## D5: colour sources, tagging and the Mac decode

**Decided.** Every camera transform from a published source (table in
[04-color.md](04-color.md#input-camera-spaces)); D-Log M through DJI's LUT and then the
inverse output view into the working space, because its curve is not published; platform
deliverables tagged 1-1-1, limited range; the viewer offers a "reference" and an "as a
Mac plays it" mode; the viewing condition is chosen per project.

**Evidence** (`docs/research/color-sources.md`, `bench/cvtest/`).
- S-Log3 code values are absolute 10-bit codes and Sony states S-Log3 is recorded full
  range in XAVC. The file's range governs YCbCr decode; the curve reads code values.
- DJI's D-Log M downloads are Rec.709 LUTs only (33^3), one file shared by twelve
  products; no curve or gamut published.
- YouTube and Vimeo document BT.709 (H.273 value 1) for SDR; YouTube assumes BT.709 for an
  unspecified transfer and converts full range to limited.
- macOS 26.4.1 decodes 1-1-1 and 1-2-1 identically with gamma 1.9609375, 1-13-1 with the
  sRGB curve, and 1-4-1 at about gamma 1.79.

*Amended after review (D9):* the first version said data levels were "per curve"
(wrong: range belongs to the file), sent D-Log M through `Camera Rec.709` (which would
tone-map DJI's rendering twice), and said no tag changes the Mac decode (1-13-1 does).

## D6: decoder chosen per codec and verified; matrix and range always explicit

**Decided.** At import, decode a stretch in software and with VideoToolbox and compare
frame hashes; use hardware only when bit-exact. The YCbCr matrix and range are passed
explicitly from the media record; the colour space comes from the camera sidecar before
stream tags.

**Evidence** (`bench/decode.sh`, `bench/matrix_check.py`).
- 4:2:2 10-bit H.264: VideoToolbox outputs 693 frames for 705 packets, so software
  (about 210 fps).
- 4:2:2 10-bit HEVC stand-in: VideoToolbox bit-exact on 705 of 705 frames with frames kept
  in hardware surfaces (so a software fallback could not pass), 0.3 cores busy against
  12.4 for software.
- The user's S-Log3 clip carries no matrix, primaries or transfer tag in its stream (only
  `color_range=pc`); only the Sony XML says S-Log3, S-Gamut3.Cine and a Rec.709 matrix.
  Decoding it with ffmpeg's default matrix is bit-identical to BT.601 and differs from
  BT.709 by up to 0.0155 of full scale.

*Amended after review (D9):* the first version read the H.264 VideoToolbox result as "70
fps, slower", from a script that divided the packet count by time and never checked the
frames; it was a broken decode.

## D7: proxies are ProRes Proxy 10-bit with their own decode record; time is counted in flicks

**Decided.** ProRes 422 Proxy, `yuv422p10le`, all-intra, written in limited range on
purpose and recorded as such, sized for the framing (960x540 for 16:9 comps, 1080 lines
when 9:16 is cut from 16:9). Time is an integer count at 705,600,000 per second, written
in files as literals with units.

**Evidence.** Proxy build at 129 to 189 fps across runs, 26 to 28 MB per 23.5 s at 540p, 21 to 22 ms per
random seek including process start. A plain scale converted the full-range S-Log3
original to limited range silently (mean luma 213.96 to 247.48, 247.2 predicted), which
is why the proxy carries its own record and a parity test.
The tick divides exactly into frames at every rate in the table in
[03-project-format.md](03-project-format.md#the-unit) (23.976 to 240 fps, including the 1000/1001
rates, and samples at 44.1, 48 and 96 kHz (checked by computation).

## D8: graphics runtime: web layers first, native Skia layers second

**Decided.** One layer interface (render frame N at a size, in a declared space, as
premultiplied RGBA) with two implementations. Tier 1, built first: web layers rendered by
one pinned Chromium build, used both by the editor (live) and by the capture pool (final).
Tier 2, built when a measured need appears: the same layer model rasterised by Skia in the
engine, in float16.

**Evidence** (`bench/graphics/`, [05-motion-graphics.md](05-motion-graphics.md#what-was-measured)).
Seek-then-screenshot capture with four pages reaches 48 fps at 1080x1920 and 19 fps at
4K, fourteen times the previous pipeline. Skia renders the same scene at 279 to 313 fps
and 40 to 43 fps in one process, with float16 linear nearly free. macOS Chromium has no
`BeginFrameControl`; Chrome rejects linear canvas colour spaces.

**Where this departs from the evaluation's own recommendation.** The engine evaluation
recommended Skia as the core and the web as an escape hatch, on throughput and float
colour. Web comes first here instead, because:
- the catalogue (D4) is overwhelmingly DOM, CSS and Three.js, which Skia cannot render;
  porting it to Skia is the expensive path, porting it to the web-layer contract is close
  to free;
- 48 fps already makes graphics a minute-scale part of a short-form final render;
- live editing at the playhead is better in the browser than through engine round trips;
- titles and UI-style graphics are display-referred overlays, where 8-bit sRGB is what
  design tools deliver anyway.
Skia stays the planned path for scene-referred graphics, heavy 4K graphics and Lottie.

**Rejected.** Remotion (licence), GSAP bundled in any form (licence), Theatre.js studio
(AGPL-3.0, dormant), Motion Canvas and Revideo (replay-from-start seeking, little
maintenance), lottie-web as a core (maintenance mode), Vello (no Python path yet).

**Risks.** 8-bit alpha on soft glows; Chromium version drift between editor and capture
(hence one pinned build); skia-python's single maintainer (pinned wheels, thin interface).

## D9: design review, findings adopted

A red-team review of the first draft re-ran the benchmarks and found errors that came
from the draft's own rules. Each was reproduced here before being adopted. The review
and its reproduction status are recorded in `docs/research/design-review-2026-10-10.md`.

| finding | evidence (reproduced) | change |
| --- | --- | --- |
| H.264 VideoToolbox drops frames | 693 of 705 frames | per-codec decoder with hash check (D6) |
| HEVC VideoToolbox is exact and frees the CPU | bit-exact, 0.2 to 0.3 against 12.4 cores | hardware for HEVC (D6) |
| the proxy silently changed range | mean luma 213.96 to 247.48 | explicit conversion, own decode record, parity test (D7) |
| the output view does not need the GPU | 144 ms at 4K on 15 threads | CPU OCIO in year one (D2) |
| a baked LUT fails on colour | 65^3: p99 5.8, max 77 codes | dense, saturated test patterns ([04-color.md](04-color.md#precision-and-tests)) |
| OCIO ships the grading operators | `GradingPrimary`, `Tone`, `RGBCurve`, `HueCurve`, `CDL`, `File` transforms present in 2.6 | grading from OCIO (D3) |
| display layers after the view broke stack order | design inspection | top overlays after the view, the rest through the inverse view (round trip median under 0.15, max 11 codes) ([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)) |
| "data levels per curve" was a misreading | colour-science discussion #786; Sony's absolute code values | range from the file (D5) |
| tags do change the Mac decode | 1-13-1 decodes with the sRGB curve | tagging section rewritten ([08-deliver.md](08-deliver.md#tags)) |
| speed and remap as two stores cannot round-trip | the integral of a bezier is not a bezier | source time canonical, speed compiled at placement ([03-project-format.md](03-project-format.md#time-remap)) |
| exit criteria passable without the feature working | M0 trivially passable, M2 self-referential, M3 a single re-cut | roadmap rewritten with a Phase 0 baseline and a Phase 1 spike ([10-roadmap.md](10-roadmap.md)) |

Also adopted: audio clock from `getOutputTimestamp()`; content hashes instead of revision
numbers in files; normalization before the true-peak limiter; audio muxed once over
joined chunks; downbeats from Beat This! or madmom; vector motion blur instead of
averaging RIFE sub-frames; flow estimated on a display-rendered copy; comp-relative units
for pixel-sized parameters; LUT references that name their spaces; `lc verify` checking
bias and cut times, not only PSNR against its own render.

Reproduced later (2026-10-11): an untagged H.264 clip is given SMPTE-C primaries at 256x64
but BT.709 at 720x480 and 1920x1080 (`bench/cvtest/make_clips.sh` and the `pl` probe).

## D10: licence (open)

Proposed: MIT, like Pickapicka. It decides things already in the design: OpenDRT
(GPL-3.0) cannot ship, an app bundle needs an LGPL ffmpeg (losing x264), and PyAV's
bundled LGPL FFmpeg is acceptable. The user's decision.

