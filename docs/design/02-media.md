# Media: ingest, colour-space detection, proxies, decode

## Ingest

Importing a file does four things and changes nothing on disk next to it:

1. **Probe** with ffprobe: codec, profile, pixel format, size, rate (as a rational),
   frame count, stream colour tags, audio layout, creation time, rotation, timecode.
2. **Read sidecars and embedded vendor metadata** (Sony `NNNNM01.XML`: colour, rates,
   recording mode, timecode; DJI to be determined).
3. **Decide the colour space**, with the evidence that decided it (below).
4. **Build the proxy** (10-bit all-intra, in the original's range, sized for the framing) into the project's
   cache. Music is decoded once to a PCM WAV instead ([music and beats](#music-and-beats)).

The result is a media record in the project, one file under `media/` (see
[03-project-format.md](03-project-format.md#media)), that names the original by a path
relative to a media root, and holds the proxy, the decided colour interpretation and the
evidence for it.

Photos (JPEG, PNG, TIFF) are imported where they lie too, with a record of their own
header and no decisions: they are shown as graphics pages show images
([05-motion-graphics.md](05-motion-graphics.md#photos)).

## Colour-space detection

The stream tags alone cannot be trusted, and on the footage this tool is for they are
often empty. Measured on the user's own clips (Sony ILCE-7CM2):

| clip | stream tags (ffprobe) | Sony XML |
| --- | --- | --- |
| S-Log3 sample (clip A in `bench/RESULTS.md`) | `color_space=unknown`, `color_transfer=unknown`, `color_primaries=unknown`, `color_range=pc` | `CaptureGammaEquation=s-log3-cine`, `CaptureColorPrimaries=s-gamut3-cine`, `CodingEquations=rec709` |
| HLG sample | `bt2020nc`, `arib-std-b67`, `bt2020`, `tv` | `hlg-slog3ootf`, `rec2020`, `rec2020ncl` |

An S-Log3 file says nothing about S-Log3 in its own streams. Only the sidecar does.

Detection walks a fixed chain and stops at the first source that answers:

1. **Camera sidecar.** Sony XML: `CaptureGammaEquation` and `CaptureColorPrimaries` give
   the colour space; `CodingEquations` gives the YCbCr matrix.
2. **Embedded vendor metadata.** DJI writes per-file metadata into the MP4; which field
   names the colour mode is not known yet and needs a sample clip from each DJI body.
3. **Stream tags**, when they are specific (HLG and PQ are tagged; log usually is not).
4. **Nothing found, or evidence with no mapping row: undecided.** The import still
   succeeds, the media record says `undecided` with every piece of evidence it found and
   the reason ([03-project-format.md](03-project-format.md#media)), `lungecuta detect` exits
   non-zero, and the clip cannot be placed on a timeline until someone decides it.
   Rec.709 is never assumed.

The mapping from evidence to an OpenColorIO colour space is a table in code with one row
per observed combination. Evidence that matches no row makes the clip undecided; it is
never matched to a nearby row. Rows are added only from a real file.

The Sony sidecar is looked for only as `<stem>M01.XML` in the clip's own directory, the
name the camera writes.

| evidence | colour space | status |
| --- | --- | --- |
| Sony `s-log3-cine` + `s-gamut3-cine` | `S-Log3 S-Gamut3.Cine` (OCIO studio config) | seen on disk |
| Sony `s-log3` + `s-gamut3` | `S-Log3 S-Gamut3` | expected, no file yet |
| Sony `rec709` + `rec709` | `Camera Rec.709` (OCIO studio config): the inverse of the camera's Rec.709 curve, into scene light like the log clips ([04-color.md](04-color.md#input-camera-spaces)) | seen on disk: three clips of a 23.98p shoot from the same body (2026-10-12), their streams tagged BT.709 throughout, limited range |
| Sony `hlg-slog3ootf` + `rec2020` | **none yet: the clip stays undecided** | seen on disk. OCIO's config has HLG only as a display, not as a scene-referred input, and what Sony means by `slog3ootf` is unknown, so no mapping row exists and the import correctly reports the clip as undecided ([04-color.md](04-color.md#input-camera-spaces)) |
| Sony S-Log2 | not in the OCIO config; defined from Sony's published formula | no file yet |
| DJI D-Log | `D-Log D-Gamut` (OCIO studio config), per body once a grey-scale shot confirms the white paper's code values | no file yet |
| DJI D-Log M | DJI's official LUT, curve not published | no file yet |
| DJI D-Log 2 (Osmo Pocket 4P) | DJI's official LUT once obtained, curve and gamut not published | no file yet; no DJI LUT found either ([04-color.md](04-color.md#input-camera-spaces)) |
| Apple Log | `Apple Log` (OCIO studio config) | no file yet |

### Rates and timecode

The Sony XML also carries the clip's rates and timecode. Checked on the S-Log3 sample and
on two HLG samples (2026-10-11): `<VideoFrame captureFps="29.97p" formatFps="29.97p">`
(`59.94p` for both on the 60p HLG clip; `23.98p` for both on every clip of a 23.98p shoot,
2026-10-14, whose streams are 24000/1001), `<RecordingMode type="normal">`, and an
`LtcChangeTable` whose first entry agrees with the timecode tag ffprobe reads from the
data stream (00:00:21:15 on the S-Log3 sample). They go into the media record's `time`
entry:

- **`start_tc`** is kept for interchange. Resolve and FCPXML address a clip's frames by its
  timecode, so the FCPXML exporter needs it, while lungecuta's source time counts from the
  first presented frame ([03-project-format.md](03-project-format.md#literal-grammar)).
  Whether Resolve places FCPXML in points by timecode is checked in Phase 0.
- **`capture_rate`** would differ from the file's rate in slow-and-quick recording, where
  the camera captures at one rate and writes another. That is expected from the field
  names; no such file is on disk, so the values Sony writes are unverified. Speeds stay
  relative to the file's rate, so an S&Q clip shot at 119.88 and written at 29.97 plays as
  slow motion at 100 %; the capture rate is kept so the UI can show real-time speed.
  *(proposed)* The first S&Q file confirms the fields before this is relied on, and until
  then a clip whose two rates differ imports with `time.status: undecided`, like a colour
  space nobody has decided.

### Carrying the decision through the ingest scripts

The existing ingest scripts (stabilization, rolling-shutter correction, reframing) re-encode
clips. Their outputs have no XML next to them and copy the original's stream
tags, which for S-Log3 are `unknown`. So detection runs on the camera original, and a
derived file inherits the original's decision through the stage's manifest. A derived
file whose original cannot be found is undecided, like any other. `lungecuta stabilize`
is such a stage inside lungecuta: its record names the original (`derived_from`) and how
the clip was made (`derivation`: Gyroflow's version, preset and output, and the original's
hash then), and takes the original's colour and time decisions with that in their
evidence, once the clip is checked to hold every frame at the original's rate and range.

## Decode

- **The decoder is chosen per clip and verified at import.** The first 300 frames (or the
  whole clip if shorter) are decoded in software and with VideoToolbox at once and compared
  frame by frame, byte for byte, in the clip's own pixel format; hardware is used only when
  every frame is identical, and the result is stored in the media record's `decode` entry
  ([03-project-format.md](03-project-format.md#media)). The check stops at the first frame
  that differs or that VideoToolbox reports an error on, and records how many frames it
  compared. (Until 2026-10-13 each decode was hashed by ffmpeg's `framemd5`, which spent
  most of the check's 23 s on the 4K sample hashing; records made then say
  `"method": "framemd5"` and remain valid.) With `-hwaccel videotoolbox` alone,
  ffmpeg can fall back to software without saying so, which would make the comparison
  software against itself; the check therefore keeps frames in hardware surfaces
  (`-hwaccel_output_format videotoolbox_vld` with `hwdownload`), so that a fallback fails
  instead of passing. Checked: on the HEVC stand-in this forced path decodes all 705 frames
  bit-exact with software; on the H.264 4:2:2 clip it fails with "hardware accelerator
  failed to decode picture". That failure is the camera stream's, not the format's:
  libx264's progressive 10-bit 4:2:2 decodes identically in VideoToolbox, interlaced 4:2:2
  is refused at setup, and ProRes differs from the software decode
  (`tests/test_decode.py`); why the camera's stream fails is not established, which is
  why the check is per clip. Measured (`bench/decode.sh`):
  - 4:2:2 10-bit H.264 (XAVC S): VideoToolbox outputs **693 frames for 705 packets**, so
    software decode, at about 210 fps;
  - 4:2:2 10-bit HEVC (the XAVC HS stand-in): VideoToolbox is bit-exact on all 705 frames
    and keeps 0.3 cores busy against 12.4 for software, so hardware, leaving the CPU to
    OpenColorIO.
- **Y'CbCr becomes R'G'B' in lungecuta, not in ffmpeg.** ffmpeg hands over the decoder's
  own planes (raw output in the stream's own pixel format, which is the decoded frame
  byte for byte: its md5 is ffmpeg's framemd5, `tests/test_decode.py`); numpy brings chroma
  to every pixel by linear interpolation from where the stream says the samples sit
  (`chroma_location`, ITU-T H.273 V4 Table 8; the S-Log3 and HLG samples say `left`, a
  stream that says nothing is an error for 4:2:2 and 4:2:0), and applies the range and the
  matrix by H.273 V4's equations (`docs/research/color-sources.md`, section 7), keeping
  values below black and above white. The interpolation filter is a choice (*proposed*):
  linear keeps flat areas exact and adds no overshoot; a sharper one would differ only at
  chroma edges. ffmpeg's scale filter was found wrong twice and is no longer used to
  decode: its conversion to `rgb48le` is off by a gain (nominal white 3.9 ten-bit codes low
  in limited range, 1.0 in full range, about 2 at mid grey, values below 0 clipped;
  `bench/swscale_range.py`), and its 10-to-16-bit Y'CbCr output replicated the top bits of
  luma on a 960x540 frame of the S-Log3 proxy (104 became 6662, not 104 x 64 = 6656) while
  it shifted chroma, and shifted both on an 8x2 test frame (why, not established). Every number in `bench/RESULTS.md` measured before
  2026-10-13 was decoded through it.
- **The YCbCr matrix and range are always passed explicitly**, from the media record.
  For an untagged clip ffmpeg's scaler assumes BT.601 (`bench/matrix_check.py`): on the
  S-Log3 clip above, whose XML says Rec.709, the default decode is identical to forcing
  BT.601 and differs from BT.709 by up to 15.9 ten-bit codes. On log footage that is a
  visible hue error.
- **Range is the file's, and the curve consumes code values.** YCbCr is decoded with the
  file's actual range, for luma and chroma alike; the log curve then reads absolute 10-bit
  code values (S-Log3 black at 95, grey at 420; S-Log2's paper describes the same kind of
  absolute codes through an IRE formula). Decoding a full-range file as legal to suit a
  formula would mis-scale chroma by 896/1023. The S-Log3 clip is flagged full range,
  which agrees with Sony's statement that S-Log3 is recorded full range in XAVC; reading
  its black as legal instead would move it from 0.093 to 0.035 of full scale.
- **Data levels are to be verified on a chart, not inferred.** A lens-cap shot and a colour chart
  from each body, with raw Y and chroma codes read directly, settle range and matrix
  without depending on Resolve's conventions; a frame through Resolve's Color Space
  Transform is a second check that includes saturated patches.
- **Final render reads sequentially.** Source frames are decoded in order into float32;
  4K arrives at 52 fps (`bench/pipe_read.py`). A speed ramp reads source frames in
  increasing order almost everywhere, so long-GOP originals cost nothing extra.
- **Random access goes to the proxy for preview.** Final-quality stills (`lungecuta still`) read
  originals at arbitrary frames: in Phase 0 through the ffmpeg CLI with accurate seeking,
  checked against the same frame index from a sequential decode; PyAV only after its
  bundled FFmpeg has passed the same framemd5 check against the CLI, which it has on the
  S-Log3 sample and its proxy (`bench/pyav_check.py`). ffmpeg's input seek
  returns the first frame at or after the seek time, so frame N is reached by seeking to
  half a frame before it; a seek to N itself is at the mercy of rounding, and half a frame
  after lands on N + 1. Checked on frames 0, 1, 13, 352 and 704 of the S-Log3 sample (a
  `media` test). One 4K still costs about 1.6 s as a process, 0.6 s of it the
  seek and decode, and a 540-line one 0.8 s (`bench/still_cost.py`). A contact sheet reads its frames in one
  decode from the start instead (`lungecuta sheet`): at every 30th frame of the S-Log3 sample
  that is about four times faster than a seek per frame, with identical frames
  (`bench/sheet_scope_cost.py`).
- **Every decode is in the coded orientation** (`-noautorotate`). ffmpeg otherwise applies
  a rotation tag on decode: a rotated 3840x2160 frame arrives as 2160x3840 with the same
  byte count, which the first parity measurement read as garbage (p99 440 codes on the HLG
  sample, whose rotation is 90) and which squashed its proxy.

## Proxies

- **A proxy can wait until its clip is used.** At 534 MB for 23.5 s, a shoot of 27
  minutes would need about 29 GB of proxies, and the proxy and its parity test are most
  of an import's time (`bench/RESULTS.md`, "What a footage import costs"), while the
  editor previews only the clips a comp shows. `lungecuta import --no-proxy` skips it, and
  `lungecuta proxy KEY...` or `--comp COMP` makes it later, with the same parity test, for
  the clips named or shown; stills, sheets and renders read the originals and need none.
- **The format is chosen by the parity test below, not by size or speed**, and the test
  was run on both samples before anything was fixed (`bench/proxy_parity.py`,
  `bench/RESULTS.md`, "Proxy parity"). No ProRes profile passed: at 540 lines the codec's
  loss on noisy S-Log3 alone is p99 8 codes even for 4444, and 4:2:2 chroma adds more. The
  one candidate that passed on both samples is **lossless FFV1 4:4:4, `yuv444p10le`,
  all-intra (`-g 1`), in a Matroska file**, and `lungecuta import` uses it, *provisionally*: it is
  19 times the size of ProRes Proxy (534 MB against 28 MB for the 23.5 s S-Log3 sample),
  and one seek costs about 135 ms per ffmpeg process against 23 ms. Which way to go is the
  user's decision (AGENTS.md, open decisions). Size comes from the project's
  `proxy_height` (540 by default, set at `lungecuta init`), and should follow the framing in use:
  540 lines for 16:9 comps, 1080 when a 9:16 comp is cut from 16:9 sources (a 540-line
  proxy leaves only a 304x540 crop for a 1080x1920 viewer). The proxy keeps the coded
  frame's shape, and a width that is not a whole even number is an error rather than a
  rounding. Changing the setting rebuilds the proxies. Build cost and quality at 1080
  lines are still to be measured.
- **Proxies are built from lungecuta's own decode** (`build_proxy`): the decoder's planes,
  chroma brought to every pixel as above, each plane area-averaged to the proxy size in
  code units (the Y'CbCr to R'G'B' conversion is affine, so it commutes with an average),
  the range changed by H.273 only if asked, rounded to 10-bit codes and handed to the
  encoder. A lossless proxy then decodes to the downscaled original up to that rounding,
  which bounds the difference at 1.48 codes in R and 1.64 in B in limited 10-bit
  (`tests/test_proxy.py`); measured on both samples, the lossless proxy's maximum is that
  bound and its bias under 0.1 code. An earlier version scaled with ffmpeg, whose chroma
  handling differed from the decode's by p99 3.3 codes at 540 lines on the S-Log3 sample.
  Building this way runs at about 30 fps for a 4K original, against 100 to 180 with
  ffmpeg's scaler.
- **Every proxy has its own decode record.** Building a proxy with a plain scale silently
  converted clip A from full to limited range (mean luma 213.96 in the source, 247.48 in
  the proxy, 247.2 predicted for full to limited). The range is therefore always stated
  in the record. **The proxy keeps the original's range**, since there is then nothing to
  convert. A conversion by H.273 is free (limited proxies of the full-range sample and
  full proxies of the limited-range one measure as well as range-keeping ones); the most
  of a code of bias the first measurements found came from ffmpeg's scaler doing the
  conversion (`bench/RESULTS.md`, "Proxy parity"). Its log curve
  and gamut are inherited from the original's record, since H.273 has no code points to tag
  S-Log3 or S-Gamut3.Cine in the file. Proxies are decoded in the coded orientation
  (`-noautorotate`; rotation is applied in the render's geometry stage).
- **Parity test**, run at import and stored in the proxy record: eleven frames (first,
  last, and nine at evenly spaced positions); each decoded from the original with its own
  record and area-downscaled to the proxy size, against the same frame decoded from the
  proxy with the proxy's record. Recorded, in 10-bit codes over all pixels and channels:
  p99, maximum, mean signed bias per channel, and p99 after averaging 4x4 blocks; once in
  the file's own R'G'B' codes and once in DaVinci Intermediate.
  - **The gate is provisional:** in R'G'B' codes, p99 at most 2 and no channel biased by
    more than half a code, or the proxy is deleted and the import stops. *(proposed)* The
    bias limit catches a range or matrix error before the spread does: decoded through a
    scaler whose range conversion was off, a proxy showed a bias near one code while its
    p99 stayed near 2.
  - **The criterion as first written, p99 at most 2 in DaVinci Intermediate, cannot be met
    by any proxy built this way**: even the lossless proxy is at p99 3.94 DI codes on the
    S-Log3 sample, with a bias of about +0.55, while it is at 1.24 in R'G'B' codes. A proxy
    is downscaled before the input transform and the reference after it, and on noisy
    pixels the average of a curve is not the curve of the average. Meeting it would mean
    proxies stored after the input transform, in the working space. The DI numbers are
    recorded so that the decision can be made on them.
  - A clip whose colour space is undecided gets a proxy but no parity numbers, since the
    matrix to decode it with is part of the decision.
- Rebuilt only when the original's content hash changes.

## Music and beats

Music files get a beat map on import: beat times, downbeats, tempo, analyzer name and
version. librosa tracks beats but not downbeats, and bars are first-class here, so the
tracker is **Beat This!** (CPJKU, ISMIR 2024, `pip install beat-this`, on PyTorch), D14.
Its README (checked 2026-10-11): "The code and the published model weights are released
under the MIT license", with a warning that some training files are copyrighted or under
limited Creative Commons licences. madmom, the other tracker with downbeats, is excluded:
its code is BSD, but "all model and data files are distributed under the" CC BY-NC-SA 4.0
licence (madmom README), which a tool meant to be MIT-licensed cannot ship. Tempo is
re-derived from the mean beat interval, because librosa's estimate is quantized.

**Music is decoded once, at import, to a 48 kHz PCM WAV in the cache** with ffmpeg
(librosa cannot open MP4/MOV audio). The analyzer, the engine's mix and anything handed
to Resolve in Phase 0 all read that one file, so a compressed original's encoder delay is
resolved in one place; Resolve decoding an MP3 or AAC itself could place it differently
(not measured). The map is data in the project and can be nudged by hand; a nudged beat
is marked as manual so re-analysis does not overwrite it, and re-analysis follows the rule
in [03-project-format.md](03-project-format.md#beat-maps).

How it is done (`src/lungecuta/music.py`):

- **Decode:** ffmpeg decodes the first audio stream to float samples at the file's own
  rate. Its decoders remove the encoder delay where the file states it: AAC in MP4, MP3
  with a LAME header and FLAC, each encoded by ffmpeg from a known signal, decode at a lag
  of 0 samples against it (`tests/test_music.py`); AAC keeps its padding at the end.
- **Resample:** to 48 kHz by soxr at its default quality (`HQ`), the resampler Beat This!
  uses itself, since this ffmpeg build has no libsoxr; a 48 kHz file is not resampled.
- **WAV:** 32-bit float (`pcm_f32le`, with the `fact` chunk a non-PCM WAV needs), so a
  decode that goes over full scale is kept rather than clipped; the record holds its peak.
  Whether Resolve reads a float WAV as it is is checked on the Phase 0 import.
- **Analysis:** Beat This! 1.1.0 with its default checkpoint (`final0`) and its default
  `minimal` postprocessing (the DBN one needs madmom), on the CPU, since PyTorch MPS has
  not been checked against the CPU. The model works at 50 frames a second, so beat times
  fall on 20 ms steps (10 ms where two frames are averaged), and it moves each downbeat
  onto the nearest beat, so downbeats are stored as beat indices. The checkpoint (81 MB)
  is downloaded by torch.hub on first use and its hash is recorded.
- **Measured** (`bench/beats_check.py`): on a synthetic 120 BPM drum track every beat is
  found within 10 ms, with every downbeat, at 48 and at 44.1 kHz, plus one beat in the
  tail after the last bar; a 124.9 s MP3 decodes in 0.1 s and is analysed in 1.2 s.

## Integration with existing tools

The existing ingest scripts are private and outside this repository. What lungecuta takes
from each stage:

| stage | how it connects |
| --- | --- |
| classification | its manifest (transcripts, categories) is imported as clip metadata and keywords |
| gyro stabilization (Gyroflow), rolling-shutter correction | `lungecuta stabilize` runs the pinned Gyroflow and imports its output as derived media of the original ([06-editing.md](06-editing.md#stabilization)) |
| reframing | first as derived media; later its point tracker becomes an engine effect whose output is crop keyframes ([06-editing.md](06-editing.md)) |
