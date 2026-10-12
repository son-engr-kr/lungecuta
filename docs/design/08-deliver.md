# Deliver

## Presets

A preset names everything that decides what the file is, so that `lc verify` can check
the result against it.

| preset | video | audio | for |
| --- | --- | --- | --- |
| `master-prores-hq` | ProRes 422 HQ, 10-bit 4:2:2, MOV | PCM 24-bit 48 kHz | the archive copy and re-encodes |
| `prores-4444-alpha` | ProRes 4444 with alpha | none | graphics handed to another tool |
| `web-hevc10` | HEVC Main 10, 4:2:0, VideoToolbox, MP4 with faststart | AAC (AudioToolbox `aac_at`), 48 kHz | YouTube, Vimeo |
| `web-h264` | H.264 High, 8-bit 4:2:0, MP4 with faststart | AAC 48 kHz | Instagram and anywhere HEVC is not safe |

Labels P1 to P3 are the past projects listed in [decisions.md](decisions.md), D4.

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
  ProRes alike). Then they are checked after the fact by `lc verify`.

## Quantization

- RGB float to YCbCr uses the BT.709 matrix explicitly and a chroma filter chosen on
  purpose, not the scaler default.
- Going to 8-bit (`web-h264`) is dithered. Banding in skies and gradients at 8-bit is the
  most visible way a semi-pro render gives itself away.

## Parallel rendering

The engine renders frames in worker processes; the encoder receives them in order. For
long renders the comp is split into video-only chunks encoded independently and joined
with the concat demuxer (`-c copy`), as the P3 promo did with four workers. ProRes is
all-intra and joins anywhere; HEVC and H.264 chunks are encoded with closed GOPs so the
join is clean. Audio is encoded once, over the whole mix, and muxed onto the joined video,
because AAC chunks would each carry encoder priming and leave gaps at the joins.

## Verification

`lc verify FILE --preset P` after every render:

- probe: codec, profile, pixel format, size, rate, frame count, duration, all four colour
  tags, audio format;
- loudness: integrated LUFS and true peak with ffmpeg `ebur128`, against the preset's
  target (-14 LUFS for promos, -16 for speech-led, true peak at most -1 dBTP);
- picture: a sample of frames decoded from the file and compared with the engine's own
  reference render of the same frames. Two numbers: the mean signed error per channel on
  flat patches, which exposes a wrong matrix or range as a bias, and PSNR, which measures
  codec loss. PSNR alone would mix the two;
- edit: cut times and frame counts against the project's own edit list, and, when the
  project rebuilds an existing deliverable, against that deliverable frame by frame at
  every cut, so a render that is wrong but self-consistent cannot pass;
- for presets with faces: `lc face-audit`.

## Licences

ffmpeg is used as a subprocess. The Homebrew build includes GPL encoders (libx264,
libx265); using it this way does not affect lungecuta's licence. An app bundle would ship
an LGPL ffmpeg and use the VideoToolbox encoders for H.264 and HEVC, which loses x264,
the measured baseline above; VideoToolbox H.264 is measured against x264 on the same
material before a bundle is offered.
