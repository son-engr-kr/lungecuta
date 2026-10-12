# Media: ingest, colour-space detection, proxies, decode

## Ingest

Importing a file does four things and changes nothing on disk next to it:

1. **Probe** with ffprobe: codec, profile, pixel format, size, rate (as a rational),
   frame count, stream colour tags, audio layout, creation time, rotation.
2. **Read sidecars and embedded vendor metadata** (Sony `NNNNM01.XML`, DJI to be
   determined).
3. **Decide the colour space**, with the evidence that decided it (below).
4. **Build the proxy** (ProRes Proxy 10-bit, sized for the framing) into the project's cache.

The result is a media record in the project (see
[03-project-format.md](03-project-format.md#media)) that names the original, the proxy,
the decided colour interpretation and the evidence for it.

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
   the reason ([03-project-format.md](03-project-format.md#media)), `lc detect` exits
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
| Sony `hlg-slog3ootf` + `rec2020` | **none yet: the clip stays undecided** | seen on disk. OCIO's config has HLG only as a display, not as a scene-referred input, and what Sony means by `slog3ootf` is unknown, so no mapping row exists and the import correctly reports the clip as undecided ([04-color.md](04-color.md#input-camera-spaces)) |
| Sony S-Log2 | not in the OCIO config; defined from Sony's published formula | no file yet |
| DJI D-Log | `D-Log D-Gamut` (OCIO studio config), per body once a grey-scale shot confirms the white paper's code values | no file yet |
| DJI D-Log M | DJI's official LUT, curve not published | no file yet |
| DJI D-Log 2 (Osmo Pocket 4P) | DJI's official LUT once obtained, curve and gamut not published | no file yet; no DJI LUT found either ([04-color.md](04-color.md#input-camera-spaces)) |
| Apple Log | `Apple Log` (OCIO studio config) | no file yet |

### Carrying the decision through the ingest scripts

The existing ingest scripts (stabilization, rolling-shutter correction, reframing) re-encode
clips. Their outputs have no XML next to them and copy the original's stream
tags, which for S-Log3 are `unknown`. So detection runs on the camera original, and a
derived file inherits the original's decision through the stage's manifest. A derived
file whose original cannot be found is undecided, like any other.

## Decode

- **The decoder is chosen per clip and verified at import.** The first 300 frames (or the
  whole clip if shorter) are decoded in software and with VideoToolbox and compared frame
  by frame (`framemd5`, in the clip's own pixel format); hardware is used only when it is
  bit-exact, and the result is stored in the media record's `decode` entry
  ([03-project-format.md](03-project-format.md#media)). With `-hwaccel videotoolbox` alone,
  ffmpeg can fall back to software without saying so, which would make the comparison
  software against itself; the check therefore keeps frames in hardware surfaces
  (`-hwaccel_output_format videotoolbox_vld` with `hwdownload`), so that a fallback fails
  instead of passing. Checked: on the HEVC stand-in this forced path decodes all 705 frames
  bit-exact with software; on the H.264 4:2:2 clip it fails with "hardware accelerator
  failed to decode picture". Measured (`bench/decode.sh`):
  - 4:2:2 10-bit H.264 (XAVC S): VideoToolbox outputs **693 frames for 705 packets**, so
    software decode, at about 210 fps;
  - 4:2:2 10-bit HEVC (the XAVC HS stand-in): VideoToolbox is bit-exact on all 705 frames
    and keeps 0.3 cores busy against 12.4 for software, so hardware, leaving the CPU to
    OpenColorIO.
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
- **Random access goes to the proxy for preview.** Final-quality stills (`lc still`) read
  originals at arbitrary frames: in Phase 0 through the ffmpeg CLI with accurate seeking,
  checked against the same frame index from a sequential decode; PyAV only after its
  bundled FFmpeg has passed the same framemd5 check against the CLI.

## Proxies

- ProRes 422 Proxy, `yuv422p10le`, all-intra. Size comes from the project's
  `proxy_height` (540 by default, set at `lc init`), and should follow the framing in use:
  540 lines for 16:9 comps, 1080 when a 9:16 comp is cut from 16:9 sources (a 540-line
  proxy leaves only a 304x540 crop for a 1080x1920 viewer). Changing the setting rebuilds
  the proxies. Build cost and quality at 1080 lines are still to be measured.
- **Every proxy has its own decode record.** Building a proxy with a plain scale silently
  converted clip A from full to limited range (mean luma 213.96 in the source, 247.48 in
  the proxy, 247.2 predicted for full to limited). The proxy is now written in limited
  range on purpose (`in_range=pc:out_range=tv`, tagged `tv`), and its record says so; its
  log curve and gamut are inherited from the original's record, since H.273 has no code
  points to tag S-Log3 or S-Gamut3.Cine in the file.
- **Parity test**, run at import and stored in the proxy record: eleven frames (first,
  last, and nine at evenly spaced positions); each decoded from the original with its own
  record, converted to DaVinci Intermediate, and area-downscaled to the proxy size, against
  the same frame decoded from the proxy with the proxy's record and converted the same way.
  The 99th percentile of the absolute difference in 10-bit DI codes, over all pixels and
  channels, must be at most 2, or the proxy is rejected; the maximum is recorded too
  (chroma subsampling makes edges differ more than flat areas).
- 10-bit so that the engine's grading arithmetic on preview matches the final; PSNR of the
  proxy against the original is still to be measured.
- Rebuilt only when the original's content hash changes.

## Music and beats

Music files get a beat map on import: beat times, downbeats, tempo, analyzer name and
version. librosa tracks beats but not downbeats, and bars are first-class here, so the
tracker is Beat This! (CPJKU, ISMIR 2024) or madmom, both of which track downbeats; tempo
is re-derived from the mean beat interval, because librosa's estimate is quantized.
Audio is decoded with ffmpeg to f32le (librosa cannot open MP4/MOV audio). The map is data
in the project and can be nudged by hand; a nudged beat is marked as manual so
re-analysis does not overwrite it.

## Integration with existing tools

The existing ingest scripts are private and outside this repository. What lungecuta takes
from each stage:

| stage | how it connects |
| --- | --- |
| classification | its manifest (transcripts, categories) is imported as clip metadata and keywords |
| gyro stabilization (Gyroflow), rolling-shutter correction | outputs imported as derived media of the original |
| reframing | first as derived media; later its point tracker becomes an engine effect whose output is crop keyframes ([06-editing.md](06-editing.md)) |
