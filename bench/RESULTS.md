# Measured results

Every number the design leans on, with the command that produced it. Re-run on any
change of machine, ffmpeg or OCIO version before trusting a conclusion drawn from it.
Several entries were corrected after a design review re-ran them (see
`docs/design/decisions.md`, D9); the corrected versions are what is below.

Machine: Apple M5 Pro (5 performance + 10 efficiency cores), 24 GB. ffmpeg 8.1
(Homebrew) until 2026-10-13; from 2026-10-14 lungecuta runs its pinned 9.0.2 build
(`src/lungecuta/tools.py`, compared below), OpenColorIO 2.6.0, PyTorch 2.14.1, Python 3.12.13 and numpy 2.5.4 through uv,
macOS 26.4.1 (25E253). Measured 2026-10-10 to 2026-10-13.

## Decode, proxy build, proxy seek: `bench/decode.sh`

Clip A is a camera original: Sony ILCE-7CM2, XAVC S 4K, H.264 High 4:2:2,
yuv422p10le, 3840x2160, 29.97p, 140 Mbps, S-Log3 / S-Gamut3.Cine, flagged full range,
705 frames.

Clip B is **not** a camera original. It is clip A re-encoded by
`bench/make_hevc_standin.sh` (VideoToolbox, 4:2:2 10-bit HEVC, 100 Mbps), standing in for
XAVC HS until a real one is on disk. Its GOP structure is the encoder's.

Latest run; an earlier run agreed within 3 %:

| | clip A (H.264 140M) | clip B (HEVC 100M) |
| --- | --- | --- |
| software decode: frames out | 705 of 705 | 705 of 705 |
| software throughput, CPU | 214.3 fps, 11.9 cores busy | 143.9 fps, 12.4 cores busy |
| VideoToolbox decode: frames out | **693 of 705** | 705 of 705, **bit-exact** with software |
| VideoToolbox throughput, CPU | not usable | 120.3 fps, 0.3 cores busy (frames kept in hardware surfaces and downloaded explicitly; 104.4 fps, 0.2 cores without that) |
| build 540p ProRes Proxy 10-bit | 189.1 fps, 28.1 MB | 137.1 fps, 26.1 MB |
| 50 scattered seeks into the proxy | 22 ms each | 21 ms each |

Findings:

- **VideoToolbox on clip A's 4:2:2 10-bit H.264 is a correctness failure, not a slow
  path.** It outputs 693 frames for 705 packets ("hardware accelerator failed to decode
  picture" on every frame). It is that stream, not the format: libx264's progressive
  4:2:2 10-bit decodes identically in VideoToolbox (`tests/test_decode.py`, 2026-10-13). An earlier version of this script divided the packet count by the time
  and reported 70.4 fps for that broken decode; the script now hashes every frame
  (`framemd5`) and compares decoders.
- **For HEVC, VideoToolbox is bit-exact and nearly free on the CPU** (0.2 to 0.3 cores against
  12.4), which matters in a render where those cores are needed for OCIO and graphics.
- **Decode throughput is not a bottleneck**; the slowest correct path, VideoToolbox HEVC at 120.3 fps, is 4.0x real time at 29.97p.
- **The hardware check cannot be fooled by a silent fallback**: the script keeps frames in
  VideoToolbox surfaces (`-hwaccel_output_format videotoolbox_vld`, then `hwdownload`), so
  on clip A it fails with "hardware accelerator failed to decode picture" instead of
  comparing software with itself.
- Seek times include starting an ffmpeg process per seek.

### The proxy changes range

Building the proxy with a plain `scale=960:540` silently converted clip A from full to
limited range and tagged it `tv`: first-frame mean luma 213.96 in the source, 247.48 in
the proxy, against 247.2 predicted for a full-to-limited mapping (`64 + Y * 876/1023`).
Decoding that proxy with the original's range would lift S-Log3 black from code 95 to
about 145. The script now writes the conversion explicitly
(`in_range=pc:out_range=tv`, `-color_range tv`) and prints the proxy's tag, and the
design gives every proxy its own decode record.

## Y'CbCr to R'G'B': `uv run python bench/swscale_range.py`

ffmpeg 8.1's scale filter to `rgb48le` (flags `accurate_rnd+full_chroma_int+full_chroma_inp`,
explicit `in_color_matrix=bt709` and `in_range`, the path `lungecuta` used until 2026-10-13)
against ITU-T H.273 V4's equations, on flat frames whose codes are made from known
R'G'B'. R'G'B' in 10-bit codes (value x 1023), swscale minus H.273. Run 2026-10-13:

| input | black | mid grey | nominal white | colour bars, the full channel |
| --- | --- | --- | --- | --- |
| 10-bit limited | 0 | -1.97 | -3.93 | -3.97 to -3.99 |
| 10-bit full | 0 | -0.49 | -0.98 | -0.97 to -0.99 |
| 8-bit limited | 0 | -1.98 | -3.93 | -3.94 to -3.99 |
| 8-bit full | 0 | -2.00 | -3.98 | -3.95 to -3.96 |

- A gain short of 1: in limited range white reads 1019.07 of 1023, about 255/256 of
  full scale; 10-bit full range reads code c as c x 64 / 65535 of full scale (1023 becomes
  1022.02), a left shift rather than a scaling.
- Values below 0 (a bar's empty channels, where code rounding falls under black) are
  clipped to 0 by swscale and kept by H.273.
- Y'CbCr to Y'CbCr at 16 bits (`yuv444p16le`), which `lungecuta` used for one day to let ffmpeg
  upsample chroma, is not a shift either: on a 960x540 frame of the S-Log3 proxy luma's top
  bits were replicated into the low ones (10-bit 104 became 6662, 104 x 64 = 6656) and
  chroma was shifted, while an 8x2 test frame was shifted in both, which is why the first
  check passed; why the two differ is not established. `lungecuta` now
  takes the decoder's own planes and does chroma, range and matrix itself
  (`docs/design/02-media.md#decode`).
- Measurements in this file from before 2026-10-13 were decoded through swscale's path; the
  proxy parity below was measured again with H.273.

## Proxy parity: `uv run python bench/proxy_parity.py WORKDIR CLIP...`

The parity test of `docs/design/02-media.md#proxies` for every proxy candidate at 960x540,
on the eleven frames the test uses: the original decoded with its own matrix, range and
chroma siting, area-downscaled in float, against the proxy decoded with its own record.
10-bit codes over every pixel and channel: p99, max, mean signed bias per channel (R, G,
B), and p99 after averaging 4x4 blocks. Seek is one frame per ffmpeg process, process
start included. Run 2026-10-13 with the `lungecuta` package's own code: frames decoded
by ITU-T H.273 from the decoder's planes, and every candidate built from that decode
(each plane area-averaged, the range re-coded by H.273 where it differs, rounded to
10-bit codes, 4:2:2 candidates decimated co-sited) and then encoded (ffmpeg 8.1, OCIO
2.6.0). Speeds come from a machine also running other work and are rough.

Clip A (the S-Log3 sample; full range, BT.709 matrix, chroma sited left), in R'G'B' codes
and in DaVinci Intermediate:

| proxy | range | build fps | MB | decode fps | seek ms | R'G'B' p99 / max | R'G'B' bias | blocks p99 | DI p99 / max | DI bias |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ProRes Proxy | limited | 32.3 | 27.8 | 7825 | 24 | 23.45 / 122.1 | +0.29 +0.29 +1.14 | 7.14 | 28.28 / 130.6 | +0.88 +1.03 +1.89 |
| ProRes LT | limited | 31.7 | 100.3 | 3986 | 28 | 18.64 / 89.4 | -0.01 +0.17 +0.42 | 4.63 | 22.66 / 123.8 | +0.54 +0.84 +1.13 |
| ProRes 422 HQ | limited | 31.2 | 216.6 | 1927 | 37 | 13.28 / 69.4 | -0.05 +0.20 +0.45 | 3.60 | 15.43 / 92.8 | +0.47 +0.84 +1.12 |
| ProRes 4444 | limited | 29.9 | 324.5 | 1412 | 50 | 8.00 / 31.1 | -0.06 +0.19 +0.37 | 2.07 | 10.33 / 43.7 | +0.45 +0.83 +1.01 |
| ProRes 4444 | full | 30.7 | 325.2 | 1294 | 45 | 7.98 / 34.1 | -0.19 +0.05 +0.31 | 2.09 | 10.35 / 47.6 | +0.33 +0.68 +0.94 |
| FFV1 4:2:2 (lossless) | limited | 30.8 | 354.0 | 478 | 102 | 9.94 / 56.7 | +0.00 +0.00 -0.00 | 1.81 | 10.97 / 75.2 | +0.45 +0.54 +0.57 |
| FFV1 4:4:4 (lossless) | limited | 30.1 | 499.6 | 271 | 149 | 1.39 / 1.64 | +0.02 -0.00 -0.01 | 0.44 | 3.96 / 17.7 | +0.46 +0.54 +0.53 |
| **FFV1 4:4:4 (lossless)** | **full** | 29.7 | 525.4 | 261 | 157 | **1.24 / 1.43** | **+0.07 +0.02 +0.07** | 0.39 | 3.94 / 16.4 | +0.51 +0.56 +0.62 |

The HLG sample (210 frames, limited range, BT.2020 matrix, rotation 90; colour undecided,
so R'G'B' codes only, the matrix taken from its XML for the measurement):

| proxy | range | build fps | MB | decode fps | seek ms | R'G'B' p99 / max | R'G'B' bias | blocks p99 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ProRes Proxy | limited | 30.4 | 12.8 | 4395 | 23 | 83.49 / 370.3 | +1.31 +0.43 +2.02 | 25.52 |
| ProRes LT | limited | 30.4 | 29.6 | 3137 | 27 | 44.81 / 195.2 | +0.66 +0.24 +1.03 | 12.44 |
| ProRes 422 HQ | limited | 30.7 | 63.9 | 2041 | 34 | 20.49 / 125.9 | +0.48 +0.20 +0.76 | 8.08 |
| ProRes 4444 | limited | 30.6 | 95.2 | 1472 | 41 | 10.35 / 51.4 | +0.31 +0.13 +0.46 | 3.14 |
| ProRes 4444 | full | 29.6 | 95.9 | 1451 | 41 | 10.33 / 52.6 | -0.03 -0.17 +0.11 | 3.03 |
| FFV1 4:2:2 (lossless) | limited | 29.8 | 120.0 | 408 | 105 | 12.25 / 101.8 | +0.05 +0.04 +0.05 | 4.39 |
| **FFV1 4:4:4 (lossless)** | **limited** | 29.7 | 156.4 | 306 | 135 | **1.38 / 1.66** | **+0.08 +0.02 +0.09** | 0.47 |
| FFV1 4:4:4 (lossless) | full | 28.8 | 163.9 | 293 | 143 | 1.20 / 1.44 | +0.00 -0.00 +0.00 | 0.47 |

Findings:

- **A lossless 4:4:4 proxy built from lungecuta's own decode is as close as rounding allows**: the
  maximum, 1.64 and 1.66 codes in limited range and 1.43 and 1.44 in full, is the bound
  that rounding each plane to a 10-bit code puts on R'G'B' (`tests/test_proxy.py`), and
  the bias is under 0.1 code.
- **Converting the range costs nothing when done by H.273**: limited proxies of the full
  range clip and full proxies of the limited range clip pass as well as range-keeping ones.
  The two earlier runs (2026-10-11 decoded through ffmpeg's RGB conversion and built with
  its scaler; the first run of 2026-10-13 decoded by H.273 but still built with the
  scaler) saw most of a code of bias from a range conversion: that was the scaler's.
- **No ProRes profile holds 2 codes at 540 lines on this footage**, not even 4444, in the
  file's own codes: the codec's loss on noisy log footage alone is p99 8 codes. Lossless
  FFV1 4:2:2 is at 10 to 12: the 4:2:2 chroma alone costs that much.
- **The DaVinci Intermediate criterion is out of reach by construction.** The lossless
  proxy of clip A is at p99 1.24 in R'G'B' codes and 3.94 in DI, with a bias of about +0.55
  in every channel: the proxy is downscaled before the input transform and the reference
  after it, and on noisy pixels the two orders disagree.
- Building from lungecuta's decode runs at about 30 fps, against 100 to 180 fps with ffmpeg's
  scaler: a 23.5 s 4K clip takes about 24 s.
- ProRes 4444 reads back as `yuv444p12le` with range `tv` whatever range was written:
  the range of a ProRes proxy can only live in its record.
- FFV1 decodes at about 260 to 300 fps at this size, but a seek costs about 140 to 160 ms
  per ffmpeg process; through PyAV with the container open it costs 5 ms (below, "PyAV
  against the ffmpeg CLI").
- The first run read the HLG sample through ffmpeg's automatic rotation: frames of the
  same byte count but transposed, p99 about 440 codes. Every decode now passes
  `-noautorotate` (`docs/design/02-media.md#decode`).

## One still: `uv run python bench/still_cost.py WORKDIR CLIP [FRAME]`

`lungecuta still --media KEY --at f:352` on clip A, 15 OCIO threads, median of three runs, in
milliseconds; the stages in-process, then the whole command as a new process (Python and
OCIO start-up included). Every run gave the same frame, and the PNG decodes back to the
frame's quantized codes. Run 2026-10-12.

| size | seek and decode | input transform | downscale | view | numbers | PNG | `lungecuta still` as a process |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3840x2160 | 593 | 6 | | 124 | 148 | 569 (15.6 MB) | 1552 |
| 960x540 (`--height 540`) | 571 | 6 | 39 | 9 | 9 | 43 (0.9 MB) | 812 |

- Seeking and decoding the 4K original is most of a small still (571 of 812 ms). How that
  splits between decoding from the keyframe before the frame and the conversion to
  `rgb48le` is not measured.
- At full size the PNG's zlib pass costs as much as the decode.
- OCIO's view at 4K, 124 ms on 15 threads, agrees with the 144 ms measured by
  `bench/ocio_threads.py` on random data.

## Sheet and scope: `uv run python bench/sheet_scope_cost.py WORKDIR CLIP`

On clip A, 2026-10-13, three runs of the script (each process time a median of three),
15 OCIO threads. The runs differ by up to 30 % for reasons not established, so all three
are given:

| what | run 1 | run 2 | run 3 |
| --- | --- | --- | --- |
| the 24 frames at every 30th, one decode from the start | 6536 ms | 4433 ms | 5381 ms |
| the same 24 frames, a seek per frame | 23476 ms | 19179 ms | 18783 ms |
| `lungecuta sheet --every f:30` (24 tiles, one page) | 7697 ms | 6762 ms | 5869 ms |
| `lungecuta sheet --every f:5` (141 tiles, four pages) | 14060 ms | 13944 ms | 11823 ms |
| `lungecuta scope --at f:352`, 3840x2160 | 1816 ms | 1933 ms | 1671 ms |
| `lungecuta scope --at f:352 --height 540` | 1259 ms | 1088 ms | 968 ms |

- The two reads gave identical frames; one decode is 3.5 to 4.4 times faster than a seek
  per frame here, which is why `lungecuta sheet` uses it. A seek costs 0.8 to 1.0 s on average
  here against 0.6 s for frame 352 alone (`still_cost.py`); why is not measured (each
  seek includes an ffmpeg process start, and frames sit at different places in their
  GOPs).
- Through the ACES 2.0 view, frame 352 has no value below 0 and some channel exactly 0 in
  9.87 % of its pixels at 3840x2160, all counted as clipped low; at 540 lines it is
  4.88 %, because the downscale averages the dark noise before the view. Clipped
  fractions are comparable only at one size.

## Beats: `uv run python bench/beats_check.py [MUSIC]`

Beat This! 1.1.0, checkpoint `final0`, `minimal` postprocessing, CPU, PyTorch 2.14.1 with
torchaudio 2.11.0. Run 2026-10-13.

| input | beats | error against the true beats | downbeats |
| --- | --- | --- | --- |
| synthetic drum track (`tests/clips.py`), 120 BPM, 48 beats, at 48 kHz | 48, plus 1 after the last bar | mean +2.1 ms, -10.0 to +10.0 | every true downbeat found |
| the same at 44.1 kHz, resampled by soxr | 48, plus 1 after the last bar | mean +0.8 ms, -10.0 to +10.0 | every true downbeat found |

The errors are the model's 20 ms frame grid: the true beats fall on odd 10 ms steps
(0.25 s plus 0.5 s steps) and are found on the frames either side.

The music sample (MP3, 48 kHz, 124.9 s): 258 beats, 65 downbeats, mean interval 0.48 s
(125 BPM), the first beat at 0.02 s a pickup before the first downbeat. ffmpeg decode
113 ms, no resampling, model load 180 ms (with the checkpoint cached), analysis 1222 ms;
`lungecuta import` of it took 2.2 s as a process. A second analysis (`lungecuta beats`) matched every
beat one to one with the same downbeats. No ground truth exists for this file.

## PyAV against the ffmpeg CLI: `uv run bench/pyav_check.py FILE...`

PyAV 19.0.1 (its bundled FFmpeg, libavcodec 63.1.102) against the Homebrew ffmpeg 8.1 CLI,
2026-10-13: every frame's planes from PyAV against the CLI's framemd5 in the stream's own
pixel format, then 40 random frames reached by a seek in a container kept open, each
checked against the CLI's md5 for its index.

| file | frames identical | sequential decode | seek, median / p95 / max |
| --- | --- | --- | --- |
| clip A's FFV1 4:4:4 proxy, 960x540 | 705 of 705 | 121 fps | 4.9 / 9.5 / 9.9 ms |
| clip A (H.264 4:2:2 10-bit, 3840x2160) | 705 of 705 | 23 fps | 100.0 / 243.7 / 244.5 ms |

- PyAV decodes these files identically to the CLI, so the condition the design set for
  using it (`docs/design/01-architecture.md`, process model) is met for them.
- A seek in an open FFV1 container costs 5 ms, against 140 to 170 ms for one ffmpeg process
  per seek: the preview reads proxies through PyAV.
- 23 fps sequential on the 4K original is PyAV's default, which decodes on one thread; the
  CLI's 210 fps uses frame threads. Not measured with PyAV's threading turned on.

## Two ffmpeg builds: `uv run python bench/ffmpeg_builds.py DIR_A DIR_B FILE...`

Homebrew's ffmpeg 8.1 against the build lungecuta pins (FFmpeg 9.0.2, Martin Riedl's
static macOS arm64 release), 2026-10-14: the framemd5 of every decoded video frame (in the
decoder's own pixel format, `-noautorotate`) and audio frame (as 32-bit float).

| file | video frames identical | audio frames identical |
| --- | --- | --- |
| the S-Log3 sample (H.264 4:2:2 10-bit, PCM) | 705 of 705 | 1128 of 1128 |
| the HLG sample (H.264 4:2:2 10-bit, PCM) | 210 of 210 | 336 of 336 |
| the music sample (MP3) | | 0 of 5204 |

- The camera originals decode identically, so the measurements made with 8.1 on them
  (decoder checks, proxy parity, stills, renders) stand for the pinned build.
- The MP3 decodes to different samples: 9,089,620 of 11,985,936 differ, by at most
  7.2e-7 of full scale (about -123 dBFS), a few float32 steps, as floating-point rounding
  in two builds of the decoder would give (the cause is not checked). Inaudible, but the
  WAV a music import writes, and so its hash, depends on the build, which is why
  lungecuta pins one.
- The second dry run below, repeated with the pinned build and no ffmpeg on the PATH,
  gave the same loudness, true peak, PSNR and cut offsets.

## Phase 1 spike: `bench/spike/`

The preview loop's kill criteria that an agent can measure (`docs/design/10-roadmap.md`,
Phase 1), with the throwaway engine, server and viewer in `bench/spike/` (README there).
Clip A's 960x540 FFV1 4:4:4 proxy; chrome-headless-shell 153.0.8010.12 through Playwright
1.63.0; OCIO threads 15. Run 2026-10-13.

**Interaction latency** (`latency.py`): from a change sent by the viewer to the next
animation frame after its pixels are drawn; a comp frame of two layers of the proxy, a
dynamic `GradingPrimaryTransform` (LOG) on the top one, a 50 % blend, the ACES 2.0 view,
then sRGB for the viewer, sent as RGBA over a WebSocket and drawn with `putImageData`.

| change | n | p50 | p95 | max | engine p50 | transport p50 | to the next frame p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| grade on a parked frame | 200 | 49.9 ms | **51.4 ms** | 52.1 | 19.8 | 17.4 | 12.5 |
| a new frame each time, both layers decoded by PyAV | 100 | 56.7 ms | **62.3 ms** | 67.2 | 37.2 | 17.4 | 2.0 |
| the same, one ffmpeg process per decoded frame | 30 | 340.0 ms | 347.0 ms | 357.3 | 320.1 | 17.8 | 2.0 |

Engine stages for a new frame, medians: decode 10.1 ms (two frames), H.273 3.0, input
transform 4.7, grade 2.5, blend 0.4, output view 11.6, to sRGB 1.8, to 8-bit RGBA 2.0.
Criterion p95 at most 150 ms at 540 lines: met with PyAV, not with an ffmpeg process per
frame. Checked: the canvas holds the bytes the engine sent for the last change of each
mode. Caveats: headless Chromium, so "the next animation frame" is the headless
compositor's, not a display's; transport (17 ms for 2 MB) is localhost.

**Cache refill** (`refill.py`): a timeline-wide grade on a 60 s comp of one layer (1798
frames cut from the proxy, looped), every frame decoded in order by PyAV and rendered to
8-bit RGB in memory.

| how | time | frames per second | real time |
| --- | --- | --- | --- |
| one frame at a time, OCIO on 15 threads | 42.4 s | 42.4 | 1.42 x |
| 4 frames in parallel, OCIO single-threaded each | 56.5 s | 31.8 | 1.06 x |
| 8 frames in parallel | 31.8 s | 56.5 | 1.89 x |
| 15 frames in parallel | 26.1 s | 68.9 | 2.30 x |

Criterion at least real time: met, for one layer. Checked: every frame arrived once, and
sampled frames are identical both ways. The cache holds 2.80 GB of 8-bit RGB for 60 s.

**On-screen colour** (`screen.py`): a frame of known codes (every code of each channel,
a 16 x 16 x 16 sample of the RGB cube, seeded random colours) and an engine frame, drawn
with `putImageData` and captured with a page screenshot: **0 codes** of difference
anywhere. Criterion within 1 code: met. What a physical display then shows is not
measured.

**Graphics parity** (`screen.py`): a title page (gradient-clipped text, a translucent
rounded card with a shadow, a bar), captured on a transparent background and composited
over the engine frame in numpy with straight alpha on the sRGB-encoded values, against
the same page live in a transparent iframe over the same frame: over the 141,066 pixels
the title covers, **max 1 code, p99 1.0**, 2.6 to 3.3 % of values differing, at t = 0.3,
0.6 and 2.0 s. Criterion p99 within 2 codes: met.

## A render and its check: `lungecuta render COMP --preset web-h264`, `lungecuta verify FILE --comp COMP --preset web-h264`

The Phase 0 check comp of `docs/research/phase0-resolve-checks.md` (3840x2160, 29.97,
476 frames: four cuts of the S-Log3 sample on beats, one at 50 %, the music sample under
them), rendered 2026-10-13 one frame at a time (OCIO on 15 threads, x264 CRF 18, AAC
256 kbps through AudioToolbox): **195 s, 2.4 frames a second**. `lungecuta verify` passed every
check: codec, profile, `yuv420p`, size, rate, 476 frames, tags 1-1-1 `tv`, chroma `left`,
48 kHz stereo; the two frames either side of every cut, decoded from the file, against
the engine's reference render: **PSNR 39.24 dB, bias +0.20 +0.05 +0.35** ten-bit codes,
each frame closer to its own reference than to the frame across the cut (RMSE 7.4 to 19.5
against 21.4 to 134.4; the margin is thin where two cuts come from the same, nearly static
shot). Loudness, reported before the master chain existed: -16.0 LUFS integrated, -3.7
dBTP true peak (the rerun with the chain is below). On a synthetic
smooth ramp the encode planes alone (no codec) come back with RMSE 0.64 codes for
`master-prores-hq` (10-bit 4:2:2) and 3.37 for `web-h264` (8-bit dithered 4:2:0), bias
under 0.11 (`tests/test_render.py`).

**Frames in flight** (`uv run python bench/render_cost.py PROJECT check web-h264`, the same
comp, 2026-10-13): 1 worker 208.4 s (2.28 fps), 4 workers 248.3 s (1.92), 8 workers 158.4 s
(3.00), 15 workers 151.7 s (3.14); every render's frames identical (framemd5) to the
1-worker render's, since dither is seeded by frame. The gain is small because the machine
is already busy: x264 alone, CRF 18, on 120 frames of noisy 4K (`testsrc2` with `noise`),
runs at 7.7 fps with 12 cores busy (15.5 s; clean `testsrc2` 89 fps), so encoding and the
colour work compete for the same cores. A faster delivery needs the VideoToolbox encoder,
which is measured against x264 before a preset uses it (08-deliver.md).

## Master chain: `uv run python bench/master_chain.py WAV TARGET...`

The music sample's cache WAV (124.85 s, 48 kHz stereo; -14.36 LUFS, -1.89 dBTP as it
comes), through `audio.master` at a -1 dBTP ceiling, 2026-10-13; pyloudnorm 0.2.0, soxr
1.1.0, scipy 1.18.1. The engine's meters and `ebur128` (on the same mastered WAV) agree to
`ebur128`'s printed precision in every row. Each encode was decoded and checked before
its numbers were taken: PCM gave back exactly the samples; AAC started at lag 0 and ran
1464 samples past the end, under -132 dBFS (the last frame's padding).

| target | gain | deepest limiter cut | engine: LUFS, dBTP | 24-bit PCM: LUFS, dBTP | AAC 256 kbps: LUFS, dBTP | time |
| --- | --- | --- | --- | --- | --- | --- |
| -14 | +0.38 dB | none | -14.00, -1.80 | -14.0, -1.8 | -14.0, **-1.6** | 0.56 s |
| -12 | +2.43 dB | -1.25 dB | -12.01, -1.00 | -12.0, -1.0 | -12.0, **-0.8** | 1.68 s |
| -9 | +8.42 dB | -7.30 dB | -9.00, -1.07 | -9.0, -1.1 | -9.0, **-0.6** | 3.67 s |

So a lossy encode adds 0.2 to 0.5 dB of true peak, more as the limiter works harder, and
a file mastered to the ceiling breaks it once encoded. `lungecuta render` therefore measures the
decoded AAC and masters again under a lowered ceiling (07-audio.md). A -6 LUFS target is
refused: more gain stops raising the loudness under -1 dBTP. A first version of the
limiter that aimed at the ceiling every pass still overshot by thousandths of a dB after
four passes on the -9 row (the over-ceiling samples halved each pass); aiming each pass
under the ceiling by the last overshoot brings it under in two.

**With the chain in a render** (2026-10-13): the Phase 0 check comp of the render section
below, `lungecuta render check --preset web-h264` with 15 workers, 141.5 s. Its mix came in at
-16.02 LUFS; the master raised it 2.02 dB with the limiter cutting at most 0.88 dB; the
first AAC encode decoded at -0.95 dBTP, so the render mastered again under -1.065 dBTP and
the delivered AAC decoded at -1.003. `lungecuta verify` passed every check, loudness included:
-14.0 LUFS integrated and -1.0 dBTP by `ebur128`, picture as before (PSNR 39.24 dB, bias
+0.20 +0.05 +0.35).

## An alpha render: `lungecuta render alpha --preset prores-4444-alpha`, `lungecuta verify`

The Phase 0 package's comp `alpha` (3840x2160, 29.97, 90 frames: the S-Log3 sample at 60 %
scale under a feathered ellipse mask, 0.9 of the layer, feather 0.05 of its height),
2026-10-13. `lungecuta verify` against the engine's reference on the frames it samples: colour
over black bias -0.007 +0.021 +0.034 ten-bit codes; the alpha's largest error 0.685
ten-bit codes, read back from ffmpeg's ProRes decoder at 12 bits (`yuva444p12le`). Half a
code of that is the 10-bit rounding the render writes; the rest comes with the 12-bit
decode, which is why `lungecuta verify` allows one code.

## HEVC Main 10, VideoToolbox against x265 and x264: `uv run python bench/hevc_quality.py REFERENCE OUT_DIR SETTING...`

The reference is the Phase 0 check comp rendered with `prores-4444-alpha` (3840x2160,
29.97, 476 frames, 15.9 s; decoded as `yuva444p12le`), 2026-10-13, ffmpeg 8.1. Every
encode decoded to 476 frames before its numbers were taken. PSNR is per plane against the
reference brought to 4:2:0 10-bit by the same conversion the encoders' input went through,
so it is the codec's loss alone (an 8-bit encode is compared after swscale's 8 to 10-bit
step, checked to be exactly four times the code over all 256 codes). Frame to frame is the
Y PSNR of every frame against the next: the reference's own is **37.79 dB**, and an encode
whose frames differ from each other less than that has smoothed away grain or motion.

Three runs on the same reference: the defaults (`vt:50 vt:60 vt:70 vt:80 x265:18 x265:22`,
before the frame-to-frame column existed: q50's is measured over frames 20 to 120 by hand,
and q80 came to 350.62 Mbps for 49.69 / 54.16 / 54.95 dB); then `x264:18 vt:60 vt:62 vt:65
vt:68 vt:70 vt:75 x265:18 x265:22` and `vt:69`, whose numbers the table gives; and
`file:RENDER` on the engine's own web-h264 render of the comp.

The footage is grainy: a dim, nearly static indoor shot, whose consecutive frames differ by
37.8 dB in Y, mostly sensor noise. That is the material 08-deliver.md asks presets to be
tested on, and it explains the first sweep's puzzle.

| setting | Mbps | PSNR Y / U / V, dB | frame to frame, dB | encode |
| --- | --- | --- | --- | --- |
| `hevc_videotoolbox -q:v 50` | 0.95 | 39.23 / 47.39 / 49.49 | 56.54 (frames 20 to 120) | 59.0 fps |
| `hevc_videotoolbox -q:v 60` | 24.62 | 40.14 / 48.14 / 50.21 | 42.88 | 62.5 fps |
| `hevc_videotoolbox -q:v 62` | 57.13 | 41.15 / 48.68 / 50.58 | 41.28 | 60.1 fps |
| `hevc_videotoolbox -q:v 65` | 75.77 | 41.72 / 48.99 / 50.80 | 40.76 | 51.9 fps |
| `hevc_videotoolbox -q:v 68` | 120.84 | 43.12 / 49.80 / 51.40 | 39.91 | 46.2 fps |
| `hevc_videotoolbox -q:v 69` | 120.84 | 43.12 / 49.80 / 51.40 | 39.91 | 63.2 fps |
| **`hevc_videotoolbox -q:v 70`** | **168.11** | **44.65 / 50.67 / 52.09** | **39.30** | 53.7 fps |
| `hevc_videotoolbox -q:v 75` | 219.52 | 46.30 / 51.80 / 52.97 | 38.92 | 55.5 fps |
| `libx265 -preset medium -crf 18` | 79.76 | 41.74 / 48.22 / 50.27 | 40.21 | 5.3 fps |
| `libx265 -preset medium -crf 22` | 19.20 | 39.78 / 47.02 / 49.46 | 42.94 | 6.8 fps |
| `libx264 -crf 18` 8-bit (web-h264's settings) | 150.63 | 43.70 / 49.66 / 51.20 | 38.33 | 18.4 fps |
| `lungecuta render check --preset web-h264` (engine dither and chroma) | 153.59 (video) | 43.57 / (49.51 / 51.03) | 38.13 | |

The q50 row is explained: its encode is nearly a still picture, smoothed. Its consecutive
frames differ by 56.5 dB against the reference's 37.8 (frames 20 to 120), its keyframes
are about 21 kB for a 4K frame and its other frames 2 to 4 kB, and a crop at 1:1 shows the
wall's grain and texture gone. It still scores 39.2 dB because the error left is the
grain itself, which differs from frame to frame by about as much; so near 39 dB PSNR cannot
tell an encode that keeps the grain from one that discards it, and the frame-to-frame
column is reported beside it. The engine's web-h264 render's chroma went through its own
decimation, not the conversion the reference was brought to 4:2:0 by, so only its Y is
comparable (its U and V are in parentheses).

`-q:v` moves in steps: 68 and 69 give byte-identical bitrates and PSNR. On this footage
VideoToolbox saves no bitrate over x264: at the first setting whose Y, U and V PSNR are
all at least web-h264's (q70) it spends 168 Mbps against x264's 151, and its frames vary
less from one to the next (39.3 dB against 38.3, against the reference's 37.8), so it
smooths more of the grain than x264 does at a lower PSNR. What it buys is ten bits (no
banding in gradients, where 8-bit needs its dither) and speed: about three times x264's
encode rate and ten times x265's. x265 at CRF 18 is the most efficient here (80 Mbps for
41.7 dB) but 5.3 fps. **`web-hevc10` takes q70** (*proposed*, src/lungecuta/encode.py),
the rule being web-h264's PSNR on every plane at the least bitrate VideoToolbox offers; how
much grain it smooths against web-h264 is for the person to judge on screen. One comp of
one shot is the evidence; a bright, detailed shot may place the settings differently.

## The web-hevc10 render and a flash check: `lungecuta render check --preset web-hevc10`, `lungecuta verify`, `lungecuta flash FILE`

The same check comp, 2026-10-13, 15 workers, nothing else running: **149.5 s** (3.2 frames
a second; web-h264's best was 151.7 s at 15 workers, above), 168 Mbps of video. `lungecuta verify`
passed every check: `hevc`, Main 10, `yuv420p10le`, size, rate, 476 frames, tags 1-1-1
`tv`, 48 kHz stereo, -14.0 LUFS and -1.0 dBTP; the frames either side of every cut against
the engine's reference: PSNR 39.24 dB, bias +0.004 +0.007 +0.074 ten-bit codes. The PSNR
is web-h264's to the hundredth although the codecs differ (Y PSNR over every frame: 44.65
against 43.70); one reading, not verified, is that the 4:2:0 chroma round trip both share
dominates that RGB number on this footage.

`lungecuta flash` (WCAG 2.2's general flash threshold, `src/lungecuta/flash.py`) on that file:
**75.9 s** for 476 frames decoded at 4K (533 by 300 cells, a 133 by 100 field), passed: 78
transitions in all, no cell over six in a second. On the web-h264 render of the same comp
it took 58.3 s with an HEVC sweep running beside it, and passed with 80 transitions. The
tests check its threshold on synthetic files: three flashes a second over the whole frame
pass and a transition every four frames at 30 fps fails, a flashing patch of 19 % of a
field passes and one of 33 % fails, flashes whose darker image is at 0.85 are not counted,
and a 1-pixel checkerboard flipping in 2-pixel cells is excepted while a 4-pixel one is
not (`tests/test_flash.py`).

## Onsets: `uv run python bench/onsets.py WAV`

SuperFlux (`src/lungecuta/onsets.py`) on the music sample's cache WAV (124.85 s, 48 kHz,
resampled to 44.1 kHz inside), 2026-10-13: **862 onsets, 6.9 a second, in 0.27 s**; the
median gap is 0.125 s, a sixteenth note at the sample's tempo of about 125 BPM, the
smallest 0.04 s. A real song has no ground truth here, so these are a sanity check; on the
synthetic drum track of `tests/clips.py` (four bars at 120 BPM) every kick, hat and the
last bass note's cut-off click is found and nothing else, each 5 or 10 ms before its hit
(`tests/test_onsets.py`). `lungecuta audio-check check` on the Phase 0 check comp: cuts 0.5 to
31 ms after their beats and -14 to +38 ms from their nearest onsets, 102 onsets in the
comp's 15.9 s.

## What a footage import costs: `uv run python bench/import_cost.py PROJECT KEY`

The S-Log3 sample (4K 4:2:2 10-bit H.264, 705 frames), 2026-10-13, after `lungecuta import` had
made its record and proxy. `lungecuta import` took 64 s; profiled by stage, 23.3 s went to the
decoder check (two ffmpeg `framemd5` runs of 300 frames: 11.8 s each, at 234 % CPU,
against 3.5 s for decoding all 705 frames to raw output, so the time was the md5 of
33 MB a frame), 21.8 s to the proxy build (12 s of dense matrix products whose operators
are almost all zeros, 6 s reading the pipe) and 8.7 s to each of the two parity tests,
which read the same eleven frames.

| stage | before | after | the output |
| --- | --- | --- | --- |
| decoder check: both decoders at once, raw frames compared byte for byte, stopping at the first that is not identical | 23.3 s | 0.6 s | the same verdict (failed); `framemd5` and the byte comparison agree on HEVC (identical) and ProRes (different) |
| proxy build: sparse operators, the pipe read ahead on a thread | 21.8 s | 9.2 s | identical, every frame (framemd5 of the two proxies) |
| parity: each frame read once for both spaces, reads overlapped | 17.4 s | 4.1 s | the same numbers, to the four decimals recorded |
| whole `lungecuta import` | 64 s | 19.9 s | |

Parity with 1, 2, 4, 8 and 15 reads at once: 8.6, 5.1, **4.1**, 4.4 and 4.3 s, the same
numbers each time; so `PARITY_READS = 4`. Each read is an ffmpeg seek, mostly a decode from
the keyframe before.

## How many files `lungecuta import` imports at once: `uv run python bench/import_jobs.py WORKDIR FILE...`

The six HLG clips of the HLG sample's shoot (five at 29.97p, one at 59.94p; undecided, so
proxies without parity tests), 2026-10-13, imported into a fresh project with N files at
once, in the order run:

| at once | 1 | 2 | 3 | 4 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| s, first sweep | 33.9 | 26.2 | 23.8 | 28.0 | 35.9 | 28.6 | |
| s, second sweep | 33.5 | 28.2 | 28.7 | 28.2 | 38.3 | 27.4 | 24.5 |

Every run's records and proxies (frame by frame) identical to the one-at-a-time run's.
Two at once saves about a quarter; three and four are within the spread of two, and each
import of a 4K clip holds several frames at once, so `IMPORT_JOBS = 2`.

## What `lungecuta verify` costs: `uv run python bench/verify_cost.py PROJECT COMP FILE PRESET`

The dry run's web-h264 render below (1080p, 462 frames, eight cuts, 18 frames sampled),
2026-10-13. It took 34 s: 25 s of it reading the 4K original by seek to render reference
frames, each rendered twice (once for itself, once as the frame across its cut from the
other side), 38 renders for 18 frames. Each reference is now rendered once, and a run of
consecutive sampled frames is one job:

| jobs at once | 1 | 2 | **4** | 8 | 15 |
| --- | --- | --- | --- | --- | --- |
| s | 19.4 | 10.8 | **8.7** | 9.2 | 8.4 |

Every report identical to the one-job report and to the report before the change; so
`FRAMES_AT_ONCE = 4` (`compose.py`), past which the time stays flat while the frames held
grow. `lungecuta sheet` samples its frames the same way: the dry run's sheet of eight tiles went
from 6.9 s to 3.3 s, the page and its numbers identical.

## What a render spends a frame on: `uv run python bench/render_stages.py PROJECT COMP FRAME 1`

Frame 100 of the dry run's comp below (the 4K S-Log3 sample in a 1080p comp, punched in,
with a look), on one thread as each render worker runs it, 2026-10-13, in ms:

| stage | before | after |
| --- | --- | --- |
| `frame_rgb` (planes, chroma upsampled, the matrix) | 93.4 | 44.9 |
| input transform (OCIO) | 44.6 | 44.4 |
| `shown`: the look (OCIO) | 29.9 | 31.5 |
| `place`: 4K to the comp | 83.8 | 26.4 |
| `finish`: the output view (OCIO, ACES 2.0) | 377.1 | 377.5 |
| `frame_bytes` | 34.3 | 34.6 |

Chroma upsampling gathered every sample with `np.take` (33 ms a plane); it now takes the
samples away from the clipped edges as slices, the same products, and gathers only the
edges (7 ms). Placement multiplied dense weight matrices that are almost all zeros, and
built them per frame in float64; they are now sparse, with identical weights, and give
identical frames (every case of `place_weights` compared, enlarging and shrinking).
The whole web-h264 render of the dry run went from 58 s to 37.5 s, its 462 decoded
frames identical to the earlier render's (framemd5). What is left is mostly the output
view, OCIO's ACES 2.0 transform on the CPU at about 180 ns a pixel; making it cheaper
(a GPU processor, or a baked LUT) would change the numbers and needs measuring first.

## An edit made as an agent makes one: `sh bench/dry_run.sh FOOTAGE MUSIC WORKDIR`

The S-Log3 sample and the music sample, 2026-10-13, every stage through `lungecuta` from an empty
project: a 1080p comp 32 beats long on the song (15.4 s), the clip placed at 0 s and split
on every bar, two parts slipped, a punch-in style on four parts, a look on all eight, a
dissolve, then the checks and the web-h264 render. Each stage's wall time, in the first
run and in a second after the speed-ups above (same day, same files, the render's frames
identical):

| stage | first, s | second, s |
| --- | --- | --- |
| import the 4K footage (probe, decoder check, proxy and its parity test) | 64 | 14 |
| import the music (WAV and beat map) | 3 | 3 |
| the edit, 30 `lungecuta op` calls | 4 | 5 |
| `lungecuta lint`, `lungecuta audio-check` | under 1 each | under 1, 1 |
| `lungecuta sheet` at every bar | 8 | 3 |
| `lungecuta render --preset web-h264` | 58 | 45 |
| `lungecuta verify` | 35 | 9 |
| `lungecuta flash` | 13 | 11 |

The second run's render took 45 s against 37.5 s when run alone (above), with web searches
running beside it; the stage times are wall times on a machine in use.

The render verified (-14.0 LUFS, -1.6 dBTP, every check passed), the flash check passed,
and every cut landed 0 to 26 ms after its beat. Building it first time round stopped on
seven things the unit tests had not met, all fixed before these numbers: an end in beats
refused by `new-comp`, beat splits of a clip `place` wrote in frames, split names
growing `.2.2.2`, a 4K clip left at its own size in a 1080p comp, the comp's last frame left
black by `place`, beat cuts and styles on a layer that starts at 0 s before the first beat,
and a split part that could not be slipped back past its layer time 0; `lungecuta audio-check`
also reported a dissolved cut at the dissolve's start, 226 ms before its beat.

## 4K into numpy: `uv run bench/pipe_read.py CLIP 300 sw|hw`

ffmpeg decode, `rgb48le` over a pipe, converted to float32 in numpy, 300 frames:

| source | throughput |
| --- | --- |
| clip A, software decode | 52.3 fps |
| clip B, VideoToolbox decode | 53.5 fps |

The final-render input path: 1.7x real time before any processing.

## YCbCr matrix of an untagged clip: `uv run bench/matrix_check.py CLIP`

Clip A has no matrix tag (`color_space=unknown`); its Sony XML says Rec.709. ffmpeg's
default decode is identical to forcing BT.601 and differs from BT.709 by up to 0.0155 of
full scale (15.9 ten-bit codes).

## OpenColorIO: `uv run bench/ocio_check.py`, `bench/ocio_threads.py`, `bench/ocio_lut_error.py`

The built-in `studio-config-v5.0.0_aces-v2.1_ocio-v2.6` contains, among others:
`S-Log3 S-Gamut3.Cine`, `S-Log3 S-Gamut3`, `D-Log D-Gamut`, `Apple Log`,
`Apple Log 2`, `DaVinci Intermediate WideGamut`, `Linear DaVinci WideGamut`,
`BMDFilm WideGamut Gen5`, displays `Rec.1886 Rec.709`, `sRGB`, `Gamma 2.2 Rec.709`,
`Rec.2100-HLG`, `Rec.2100-PQ`, `Display P3`. It does **not** contain S-Log2 or
DJI D-Log M. OCIO 2.6 also provides `GradingPrimaryTransform`, `GradingToneTransform`,
`GradingRGBCurveTransform`, `GradingHueCurveTransform`, `CDLTransform` and
`FileTransform`.

**CPU cost.** OCIO's CPU processor releases the GIL, so row bands on threads scale:

| transform | 1 thread | 15 threads |
| --- | --- | --- |
| S-Log3 S-Gamut3.Cine -> DWG/DI, 4K | 60 ms | not measured |
| DWG/DI -> Rec.1886, `ACES 2.0 - SDR 100 nits (Rec.709)`, 4K | 1674 ms | **144 ms** |
| same, 960x540 | 106 ms | **10 ms** |
| DWG/DI -> Rec.1886, `Video (colorimetric)`, 4K | 55 ms | not measured |

`ocio_check.py` also prints where 18 % grey lands: DI 0.336043 becomes display code 0.3831
through the ACES 2.0 view and 0.4894 through `Video (colorimetric)`. Its Metal shader for
the ACES 2.0 view is 17,720 characters with two 1D textures.

At 15 threads the ACES 2.0 view takes about 4.3 minutes of wall-clock time for 60 s at
30p in 4K (1800 frames at 144 ms), not the 50 minutes a single thread implies, and 10 ms
per preview frame. The first version of
this file concluded from the single-thread figure that the output transform had to run on
the GPU; that conclusion does not hold.

**Baking the view into a LUT does not meet the colour criterion** (a faster path may
differ from OCIO's CPU processor by at most 1 ten-bit output code anywhere on a dense,
saturated pattern; `docs/design/04-color.md`). Sampled trilinearly (as `grid_sample`
does), against the CPU processor, in 10-bit output codes:

| LUT | grey ramp, max | near-neutral colours, p99 | max | fraction above 1 code |
| --- | --- | --- | --- | --- |
| 33^3 | 5.98 | 15.49 | 167.7 | 0.942 |
| 65^3 | 1.34 | 5.84 | 77.2 | 0.499 |
| 129^3 | 0.34 | 2.17 | 64.5 | 0.069 |

Even 129^3, which passes on a grey ramp (0.34 codes), fails on colours (p99 2.17, max
64.5), which is why the test pattern has to be dense and saturated.

**Inverse view round trip** (display-referred code values through the inverse ACES 2.0
view into DI and back), on a 1024x1024 R-G plane at three blue levels, 10-bit codes:

| blue | median | p99 | max |
| --- | --- | --- | --- |
| 0.0 | 0.146 | 4.20 | 11.41 |
| 0.5 | 0.007 | 0.10 | 4.83 |
| 1.0 | 0.010 | 0.83 | 9.56 |

Near-exact for ordinary colours, up to about 11 codes for the most saturated ones.

**Metal.** OCIO generates MSL (`GPU_LANGUAGE_MSL_2_0`) for every processor; for the
ACES 2.0 view it needs two 1D textures, which `torch.mps.compile_shader` (buffer
arguments only) does not take directly.

## How macOS decodes tagged video: `bench/cvtest/`

`make_clips.sh` builds grey-patch clips with known tags; the Swift probes ask CoreVideo,
AVAssetReader and AVPlayer what they do with them (`bench/cvtest/README.md`). macOS 26.4.1
(25E253). AVAssetReader output, ProRes, green channel decoded to linear at the patch
codes 0.10 / 0.18 / 0.50 / 0.80 (the 0.18 patch is really code 0.1781):

| tags | linear | implied decode |
| --- | --- | --- |
| 1-1-1 | 0.01099, 0.03394, 0.25903, 0.64404 | gamma 1.9609375 (CoreVideo's `kCGColorSpaceCoreMedia709`) |
| 1-2-1 | identical to 1-1-1 | the same |
| 1-13-1 | 0.01013, 0.02661, 0.21606, 0.60205 | the sRGB curve (0.02661 against 0.02669 predicted) |
| 1-4-1 | 0.01599, 0.04480, 0.28955, 0.66748 | about gamma 1.79 |

H.264 1-1-1, 1-2-1 and an untagged 256x64 clip decode identically to ProRes 1-1-1. The
primaries macOS assigns to an **untagged** clip depend on frame size (`pl` probe): SMPTE-C
at 256x64, BT.709 at 720x480 and at 1920x1080. These probes measure decode APIs, not
pixels on a screen.

## Graphics capture and rasterisation: `bench/graphics/`

Seek-then-capture throughput for web pages in headless Chromium against skia-python, and
probes for alpha and cross-version text rendering. Results, versions and the one-scene
caveat are in `bench/graphics/README.md`; the summary the design uses is in
`docs/design/05-motion-graphics.md`.

## Colour tags on rawvideo input: `bench/rawvideo_tags.sh`

Frames piped to ffmpeg as rawvideo (`rgb48le`), encoded to 4:2:2 10-bit with
`-color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv`, then read
back with ffprobe. ffmpeg 8.1, 2026-10-11.

| encoder | output options only | with `setparams` as well |
| --- | --- | --- |
| libx265 | range `tv`, matrix `bt709`, transfer `unknown`, primaries `unknown` | all four `bt709` / `tv` |
| libx264 | same | same |
| prores_ks | same | same |

So the output options set only the matrix and the range; primaries and transfer have to
come from `setparams` on the frames.

## PyTorch MPS

Checked, not timed, and not checked for numerical correctness: on this machine
`torch.backends.mps.is_available()` is true, `torch.mps.compile_shader` compiles and
runs a hand-written Metal kernel on MPS tensors, and `F.grid_sample` on a 5-D input runs
on MPS.

## Repeated images: `uv run python bench/repeats.py CLIP...`

Nineteen clips of a real 23.98p shoot from the ILCE-7CM2 (the user's; read in place,
2026-10-14), seven of them at a shutter longer than a frame (1/4, 1/5 and 1/10 s, from the
clips' own metadata by a private script) and twelve at 1/50 s or faster. Each frame's
mean absolute difference from the one before at 160x90 grey; for each clip the period
and phase whose frames all differ more than every other frame does, and by what factor
(smallest change on the period over largest off it).

| clips | separation | period found |
| --- | --- | --- |
| seven at 1/4 to 1/10 s | 2.81 to 54.50 (1/10 s at night lowest; 1/4 s highest) | 6 at 1/4 s, 5 at 1/5 s, 3 at 1/10 s, every clip |
| twelve at 1/50 s and faster | 0.05 to 0.59 | none |

So a separation above 1, the two sets not overlapping, tells them apart with room on
both sides. An absolute threshold of one code does not: repeated frames differed by up to
0.28, new images in a dim slow clip by as little as 0.96, and frames of an ordinary still
shot by as little as 0.23, so a first version with that threshold counted 200 frames of
an ordinary clip as repeats and missed new images in two slow ones.

## Graphics in motion: `uv run python bench/graphics_motion.py OUT_DIR`

Through lungecuta's own capture (one Chromium, a context a page, `renderAt(t)` then a
screenshot), 320x96 pages at 24 fps, 48 frames, Chromium 153.0.8010.12 headless shell, on
the M5 Pro at a load of 8 to 16 from other work (2026-10-15).

Stale frames: a block at x = 8 + 4 n on frame n, four pages open at once in one browser and
captured in turn as a render captures its layers.

| drawn by | wrong captures, in order | wrong captures, shuffled |
| --- | --- | --- |
| DOM `left` | 0 of 48 | 0 of 48 |
| CSS transform | 0 of 48 | 0 of 48 |
| Canvas2D | 0 of 48 | 0 of 48 |
| WebGL2 | 0 of 48 | 0 of 48 |

Slow movement: a word in 40 px semibold moved 0.25 px a frame; the step of its
alpha-weighted centre from frame to frame.

| moved by | step, min to max (px) | moved over 47 steps (px; 11.75 asked) |
| --- | --- | --- |
| DOM `left` | 0.026 to 0.417 | 11.583 |
| CSS transform | 0.026 to 0.417 | 11.583 |
| Canvas2D `fillText` | 0.026 to 0.417 | 11.583 |
| CSS transform, `will-change: transform` | 0.245 to 0.255 | 11.749 |

Slow zoom: the same word scaled from 1 to 1.2 by a CSS transform; the step of its
alpha-weighted spread (0.207 px a frame asked), and the mean absolute difference of the
last frame's alpha from the word drawn at 1.2 still.

| scaled | step, min to max (px) | last frame against the still |
| --- | --- | --- |
| CSS transform | 0.059 to 0.400 | 0 (identical) |
| CSS transform, `will-change: transform` | 0.158 to 0.277 | 0.0115 (softer) |

So text the browser rasterises at each frame is placed on a grid of positions and moves
unevenly when it moves slowly; a composited layer (`will-change`) moves evenly but, scaled,
is an enlargement of one rasterisation. A font in the page's directory, not awaited before
`window.ready`, or first shown by `renderAt` later, was loaded by the first capture in each
case (served locally), so no capture showed a fallback font.

## Gyroflow: `lungecuta stabilize KEY --mode rolling-shutter [--preset P] [--output O] --key K`, then `uv run python bench/gyroflow_check.py PROJECT KEY K FRAME...`

The S-Log3 sample (ILCE-7CM2, 3840x2160 at 29.97p, H.264 4:2:2 10-bit, recorded with the
camera's distortion compensation and with IBIS data), Gyroflow 1.6.3 as pinned, on the M5
Pro (2026-10-15). Frames 0, 350 and 704 against the original: the mean signed difference
of each plane in 10-bit codes, the share of the frame's 8-pixel border that is black, and
the displacement of nine 256-pixel patches (rows, columns) by phase correlation.

| preset over the rolling-shutter mode | output | border black | displacement | mean signed Y; Cb; Cr |
| --- | --- | --- | --- | --- |
| `frame_readout_time` 0, `adaptive_zoom_window` 0: no warp asked for | lossless H.264 | 0.25 to 0.44 | one shift over the whole frame, 0 to 6 px, different each frame | -1.0 to -1.6; -0.47 to -0.48; -0.53 to -0.55 |
| the mode as first written (Gyroflow's dynamic zoom) | lossless H.264 | 0.003 to 0.004 | outwards from the middle, up to 16 px on frame 0 and 31 px on frame 704 | 0.07 to 1.85; -0.54 to -0.62; -0.32 to -0.45 |
| `adaptive_zoom_window` -1 (static zoom, the mode's default now) | HEVC | 0.002 to 0.004 | outwards, 26 to 32 px at the corners' columns on every frame | (4:2:0, not compared) |
| `adaptive_zoom_window` 0 | HEVC | 0.25 to 0.44 | 0 to 6 px | (4:2:0) |
| `lens_correction_amount` 0 | HEVC | 0.002 to 0.004 | up to 127 px, the lens distortion added back | (4:2:0) |

- Gyroflow is not transparent to code values: where nothing moved (frame 704 with no warp
  asked for) luma comes back a code low on average and both chroma planes about half a
  code low, on every frame, which looks like truncation where rounding was meant (the
  cause is not verified). It is not code for code.
- With no warp asked for, frames still move as a whole by up to 6 px, a different amount
  each frame; the clip carries IBIS data, and Gyroflow removing the IBIS movement would do
  this (not verified). So the rolling-shutter mode may give back some of what IBIS took out.
- Gyroflow's dynamic zoom changes the crop frame to frame (16 px at the edge on frame 0,
  31 on frame 704): the framing breathes. The static zoom holds one crop for the clip.
- On a clip the camera already corrected for distortion, a lens correction of 0 warps the
  picture by up to 127 px, adding the distortion back; 1.0, Gyroflow's default, is the one
  that leaves the lens as recorded.
- Asked for `P210LE` (4:2:2) with the HEVC encoder on the GPU, Gyroflow wrote 4:2:0.
- Time and size for 705 frames: lossless H.264 93 s (7.6 fps) and 6.79 GB; HEVC on the GPU at
  100 to 300 Mbps 29 s and 0.94 GB.
- The sound (24-bit PCM) is the original's sample for sample over all 1129128 of its
  samples; Gyroflow's file has 344 more at the end.

## A lossy encode's true peak against the master's ceiling: `uv run python bench/aac_peak_ladder.py PROJECT COMP web-h264 OUT_DIR CEILING...`

A real edit's comp (the user's, read in place; its music is not named here), 2026-10-15:
its mix mastered to -14 LUFS under each ceiling, encoded as `web-h264` encodes its audio
(AudioToolbox AAC at 256 kbps), decoded, and its true peak measured; the peak sat at the
same sample every time, a transient of the song in a crescendo.

| ceiling (dBTP) | -1.00 | -1.02 | -1.04 | -1.06 | -1.078 | -1.10 | -1.12 | -1.15 | -1.20 | -1.25 | -1.30 | -1.40 | -1.50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| encoded true peak | -0.932 | -0.932 | -0.932 | -0.932 | -0.913 | -1.020 | -1.062 | -1.171 | -1.241 | -1.300 | -1.313 | -1.370 | -1.351 |

The encoded peak stays still over 0.06 dB of ceiling and rises 0.02 dB where the ceiling
falls (twice), and its excess over the ceiling runs from -0.05 to 0.17 dB, so the secant
step the delivery search used found a slope of -0.24 and stopped. The search that only
lowers the ceiling, by the excess each pass, ends on this comp at a ceiling of -1.175 with
the delivered peak at -1.246 dBTP and the loudness at -14.0 LUFS (6.1 s for the audio).

## Numbers cited from elsewhere (not re-measured here)

| number | where it was measured |
| --- | --- |
| 2x source bitrate gives 48.5 dB PSNR against 46.5 at the source rate, against a ProRes 4444 render | the existing ingest scripts' encoder measurement (private) |
| face-identity similarity: same person 0.45 to 0.81, strangers up to 0.33, unmask at 0.55 | a past project's review notes (private) |
| past web-page renders: 0.26 to 0.33 s per 1080p frame; 30 s at 60 fps in about 8.5 min | past projects' render logs (private) |
| Pickapicka LUT apply: 286 ms for 24 MP; LUT refinement error 0.17/255 | comments in Pickapicka's `src/pickapicka/lut.py` ([repository](https://github.com/son-engr-kr/pickapicka)) |

Quote them with their origin, and re-measure here before a decision rests on one. A
private origin cannot be checked from this repository.

## A second edit made as an agent makes one: `sh bench/dry_run2.sh FOOTAGE MUSIC WORKDIR`

The S-Log3 sample and the music sample, 2026-10-13: a 16-beat comp built one `lungecuta op place`
per shot (four shots from 1, 6, 12 and 18 s of the clip, the first from 0 s up to `b:4`),
a time-remap ramp on the third, a title page on the first bar (`lungecuta op graphic`), a
synthesized click on every cut (`lungecuta op sfx --on cuts`), then the checks and the web-h264
render.

| stage | s |
| --- | --- |
| import the footage, the music, the click | 26, 3, under 1 |
| the edit, 11 commands | 2 |
| `lungecuta lint`, `lungecuta audio-check` | under 1, 1 |
| `lungecuta sheet` at every beat (16 tiles), `lungecuta still` at a cut | 6, 2 |
| `lungecuta render --preset web-h264` (7.7 s of comp) | 23 |
| `lungecuta verify`, `lungecuta flash` | 5, 5 |

Every check passed (-14.0 LUFS, -1.6 dBTP, 39.33 dB PSNR against the reference frames;
no flash). Cuts landed 10.5 to 25.8 ms after their beats. `lungecuta lint` warned twice that
consecutive clicks are the same sound, as its rule says with one sound given. The cuts'
nearest onsets were 10 to 41 ms before them: SuperFlux combines onsets within 30 ms,
keeping the first, so a click on a cut just after a drum hit is reported as the hit. The
first run stopped on one thing: `--for b:4` from `0s` has no meaning before the first beat,
so `place` and `graphic` take `--to`, the position a layer ends at. The footage import
took 26 s here against 14 s in the first dry run's second pass, on a machine in use.

Again on 2026-10-14 with lungecuta's pinned ffmpeg build and no ffmpeg on the PATH, run as
the installed `lungecuta` (`LC=lungecuta`): import 15, 2 and 1 s, the edit 2, lint and
audio-check under 1, sheet 6, still 2, render 23, verify 5, flash 6. Every check passed
with the same numbers as above (-14.0 LUFS, -1.6 dBTP, 39.33 dB PSNR, cuts 10.5 to 25.8 ms
after their beats, no flash).

