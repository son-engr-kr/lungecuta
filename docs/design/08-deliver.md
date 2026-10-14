# Deliver

## Presets

A preset names everything that decides what the file is, so that `lungecuta verify` can check
the result against it.

| preset | video | audio | for |
| --- | --- | --- | --- |
| `master-prores-hq` | ProRes 422 HQ, 10-bit 4:2:2, MOV | PCM 24-bit 48 kHz | the archive copy and re-encodes |
| `prores-4444-alpha` | ProRes 4444 with alpha | none | graphics handed to another tool |
| `web-hevc10` | HEVC Main 10, 4:2:0, VideoToolbox, MP4 with faststart | AAC (AudioToolbox `aac_at`), 48 kHz | YouTube, Vimeo |
| `web-h264` | H.264 High, 8-bit 4:2:0, MP4 with faststart | AAC 48 kHz | Instagram and anywhere HEVC is not safe |

Labels P1 to P3 are the past projects listed in [decisions.md](decisions.md), D4.

`master-prores-hq`, `web-h264` and `web-hevc10` master to -14 LUFS integrated with a -1 dBTP ceiling,
held in the delivered file ([07-audio.md](07-audio.md#the-master-chain-as-built-srclungecutaaudiopy)).
`prores-4444-alpha` is written too: the comp composited with its coverage, the output
view applied to un-premultiplied colour (a colour transform of premultiplied values is
wrong wherever coverage is partial), the top graphics composited straight over it, and
the result written as straight alpha, full range, the convention `lungecuta graphic` already
uses; whether Resolve reads it as straight is one of the Phase 0 checks. It carries no
audio; ffmpeg's ProRes decoder gives it back as `yuva444p12le`, and `lungecuta verify` compares
its colour over black with the engine's and its alpha within one 10-bit code (half a code
of rounding, and two of the decoder's 12-bit steps; 0.685 measured on a 4K render with a
feathered mask). `web-hevc10` is VideoToolbox HEVC Main 10 at `-q:v 70`, handed 4:2:0
10-bit as P010 straight from the engine's planes, undithered, with AAC as `web-h264`
(*proposed*). The setting is the lowest whose PSNR on every plane is at least web-h264's on
the check comp, whose footage is grainy (`bench/RESULTS.md`): 168 Mbps against x264's 151,
so on grain HEVC through VideoToolbox saves no bitrate. It buys ten bits and about three
times x264's encode rate, and its frames vary less from one to the next than web-h264's
(39.3 dB against 38.3, the source's 37.8), so it smooths somewhat more grain; whether that
shows is the person's to judge on screen. VideoToolbox's lower settings fail in a way PSNR
alone hides: q50 came to 0.95 Mbps and 39.2 dB as a nearly still, smoothed picture, because
on grainy footage the grain is most of the error either way.

Each exists in 16:9 and 9:16 sizes. Past deliverables used x264 at
CRF 16 to 19 with AAC 192 to 256 kbps; those are the starting points, and the final
settings are chosen by measurement: PSNR against a ProRes 4444 render of the same frames,
the method the existing ingest scripts used to settle on twice the source bitrate
(48.5 dB against 46.5 at the source rate; a private measurement, `bench/RESULTS.md`).
Grain is expensive to encode (the P1 showreel's CRF 18 came to 77 MB for 15 s), so presets are tested on grainy material.

## Tags

For SDR deliverables:

- **primaries, transfer and matrix 1-1-1 (BT.709), limited range.** This is the only
  tagging YouTube and Vimeo document. YouTube assumes BT.709 when the transfer is
  unspecified and converts full range to limited; Vimeo warns that unknown tags "may cause
  unexpected color variation". Instagram publishes no SDR specification.
- **Platforms re-encode, so tags mainly matter for files delivered directly.** For those,
  macOS behaviour is measured (`bench/cvtest/`, macOS 26.4.1): 1-1-1 and 1-2-1 are both
  decoded with gamma 1.9609375, so 1-2-1 does not avoid the lighter look; 1-13-1 is
  decoded with the sRGB curve, so tags do change the Mac decode. A direct-delivery preset
  rendered for an sRGB display and tagged 1-13-1 is therefore coherent colour management,
  not a trick, but it is offered only after QuickTime, Safari, Chrome and a Windows
  player have been checked by screen capture. Resolve's "Rec.709-A" bakes an unpublished
  curve into the pixels and Blackmagic documents it as unsuitable for professional
  deliverables. The viewer shows the 1-1-1 Mac look in advance
  ([04-color.md](04-color.md#viewing-conditions-and-the-mac)).
- Tags are set on the frames with ffmpeg's `setparams` filter, all four of them. The
  engine hands frames to ffmpeg as rawvideo, and in that case the `-color_primaries` and
  `-color_trc` output options are silently ignored: only `colorspace` and `range`
  survive, the other two come out `unknown` (`bench/rawvideo_tags.sh`, x264, x265 and
  ProRes alike). Then they are checked after the fact by `lungecuta verify`.

## Quantization

- RGB float to YCbCr uses the BT.709 matrix explicitly and a chroma filter chosen on
  purpose, not the scaler default. As written (`src/lungecuta/encode.py`): ITU-T H.273's
  equations, the inverse of the decode; R'G'B' outside [0, 1] clipped and the share of
  pixels clipped recorded in the manifest; chroma decimated co-sited with the even pixels
  by a [1, 2, 1] / 4 filter, and for 4:2:0 averaged between row pairs (H.273 type 0, the
  `left` siting the file then declares); the planes handed to the encoder as they are, so
  ffmpeg's scaler does no conversion. *(proposed)*
- Going to 8-bit (`web-h264`) is dithered. Banding in skies and gradients at 8-bit is the
  most visible way a semi-pro render gives itself away. As written: triangular dither of
  one code either way, seeded by frame, so a render repeats exactly. *(proposed)*
- The first presets (`master-prores-hq`, `web-h264`) take the past deliverables' starting
  points (x264 CRF 18, AAC 256 kbps through AudioToolbox) until the PSNR measurement above
  settles them; `prores-4444-alpha` and `web-hevc10` are written (above).

## Parallel rendering

The engine renders frames in worker processes; the encoder receives them in order. For
long renders the comp is split into video-only chunks encoded independently and joined
with the concat demuxer (`-c copy`), as the P3 promo did with four workers. ProRes is
all-intra and joins anywhere; HEVC and H.264 chunks are encoded with closed GOPs so the
join is clean. Audio is encoded once, over the whole mix, and muxed onto the joined video,
because AAC chunks would each carry encoder priming and leave gaps at the joins.

## Verification

`lungecuta verify FILE --preset P` after every render:

- probe: codec, profile, pixel format, size, rate, frame count, duration, all four colour
  tags, audio format;
- loudness: integrated LUFS and true peak with ffmpeg `ebur128`, against the preset's
  target (-14 LUFS for promos, -16 for speech-led, true peak at most -1 dBTP). The
  loudness passes within 0.2 LU of the target, the tolerance EBU R 128-2023 allows "to
  take account of measurement errors" (i); the true peak passes at or under the ceiling,
  with no tolerance added. A comp whose mix is silent is checked to be silent;
- picture: a sample of frames decoded from the file and compared with the engine's own
  reference render of the same frames. Two numbers: the mean signed error per channel on
  flat patches, which exposes a wrong matrix or range as a bias, and PSNR, which measures
  codec loss. PSNR alone would mix the two;
- edit: cut times and frame counts against the project's own edit list, and, when the
  project rebuilds an existing deliverable, against that deliverable frame by frame at
  every cut, so a render that is wrong but self-consistent cannot pass;
- for presets with faces: `lungecuta face-audit`.

## Licences

ffmpeg is used as a subprocess, and it is one pinned build, never whatever is on the PATH
(`src/lungecuta/tools.py`): Martin Riedl's static release build of FFmpeg 9.0.2 for macOS
on Apple Silicon (https://ffmpeg.martin-riedl.de), signed with its builder's Developer
ID, which lungecuta fetches from the builder on first use or with `lungecuta setup`, and
checks against the SHA-256 published beside each archive and the SHA-256 of the program
inside. Two builds were found to decode the same MP3 to different samples (by at most
7.2e-7, `bench/ffmpeg_builds.py`), so a beat map or a loudness figure depends on the
build; with one build every machine gets the same numbers. No other platform has a pinned
build yet, and `lungecuta doctor` says so.

The build is GPL v3 (`ffmpeg -L`; it holds libx264 and libx265). lungecuta does not link
it and does not redistribute it, since each machine fetches it from its builder, so its
licence does not reach lungecuta's. Mirroring it, as Pickapicka mirrors model weights,
would be redistribution and carry the GPL's obligation to provide its source; it is not
done. PyAV's wheel, through which the editor decodes proxies, holds its own FFmpeg
libraries (9.0.2 too) and calls them LGPL v3, yet ships libx264 and libx265 (in its
`.dylibs`, seen 2026-10-14); which licence that wheel is under is not resolved, and
lungecuta uses PyAV only to decode. An app bundle would ship an LGPL ffmpeg and use the
VideoToolbox encoders for H.264 and HEVC, which loses x264, the measured baseline above;
VideoToolbox H.264 is measured against x264 on the same material before a bundle is
offered.

The Python dependencies so far, from their package metadata: click (BSD-3-Clause),
pydantic (MIT), numpy (BSD-3-Clause and others, all permissive), OpenColorIO
(BSD-3-Clause), Pillow (MIT-CMU), and for the beat map Beat This! (MIT, code and
weights), PyTorch (BSD-3-Clause with Apache-2.0 and other permissive parts), torchaudio
(BSD), einops and rotary-embedding-torch (MIT) and soxr (LGPL-2.1-or-later, a dynamically
loaded library, which an MIT-licensed tool can depend on); for the editor and the master
chain, FastAPI (MIT), Starlette and uvicorn (BSD-3-Clause), Playwright (Apache-2.0), PyAV
(BSD-3-Clause; its wheels bundle FFmpeg libraries, under a licence not checked here),
pyloudnorm (MIT) and SciPy (BSD-3-Clause); for `lungecuta mcp`, the MCP Python SDK `mcp` and
`mcp-types` (MIT) with what they bring (anyio, jsonschema, PyJWT: MIT; httpx2, Starlette,
sse-starlette: BSD-3-Clause; OpenTelemetry API, python-multipart: Apache-2.0). The labels on sheets and scopes use the Aileron
Regular subset that Pillow bundles, which its author releases under CC0 ("Aileron is No
Rights Reserved", dotcolon.net, checked 2026-10-13).
