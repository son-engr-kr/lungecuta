# Measured results

Every number the design leans on, with the command that produced it. Re-run on any
change of machine, ffmpeg or OCIO version before trusting a conclusion drawn from it.
Several entries were corrected after a design review re-ran them (see
`docs/design/decisions.md`, D9); the corrected versions are what is below.

Machine: Apple M5 Pro (5 performance + 10 efficiency cores), 24 GB. ffmpeg 8.1
(Homebrew), OpenColorIO 2.6.0, PyTorch 2.14.1, Python 3.12.13 and numpy 2.5.4 through uv,
macOS 26.4.1 (25E253). Measured 2026-10-10 and 2026-10-11.

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

- **VideoToolbox on 4:2:2 10-bit H.264 is a correctness failure, not a slow path.** It
  outputs 693 frames for 705 packets ("hardware accelerator failed to decode picture" on
  every frame). An earlier version of this script divided the packet count by the time
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

## Numbers cited from elsewhere (not re-measured here)

| number | where it was measured |
| --- | --- |
| 2x source bitrate gives 48.5 dB PSNR against 46.5 at the source rate, against a ProRes 4444 render | the existing ingest scripts' encoder measurement (private) |
| face-identity similarity: same person 0.45 to 0.81, strangers up to 0.33, unmask at 0.55 | a past project's review notes (private) |
| past web-page renders: 0.26 to 0.33 s per 1080p frame; 30 s at 60 fps in about 8.5 min | past projects' render logs (private) |
| Pickapicka LUT apply: 286 ms for 24 MP; LUT refinement error 0.17/255 | comments in Pickapicka's `src/pickapicka/lut.py` ([repository](https://github.com/son-engr-kr/pickapicka)) |

Quote them with their origin, and re-measure here before a decision rests on one. A
private origin cannot be checked from this repository.
