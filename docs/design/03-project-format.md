# Project format

A project is a directory. Every file in it is JSON that a person can read and an agent
can write, validated against a schema generated from the engine's pydantic models and
committed as `schema/*.json`.

```
myedit.lc/
  project.json          settings, colour management, media roots, comp index
  media/
    C0001.json          one media record per file, named by its key
  comps/
    main.json           a composition: layers, keyframes, beat map reference
    title-card.json     a precomp
  grades/
    shot3.json          a clip's node graph (see 04-color.md)
  styles/
    drop-ramp.json      a style asset (speed + zoom + blur, beat-relative)
    warm-film.json      a look (grade nodes + LUT reference)
  graphics/
    lower-third.js      code for a code-driven graphics layer
  luts/                 LUTs the project refers to, copied in by content hash
  roots.local.json      where this machine keeps each media root; never committed
  cache/                proxies, rendered frames, analysis, the UI journal; never committed
  .gitignore            written by lc init: cache/ and roots.local.json
  AGENTS.md             the guide a coding agent reads (Codex), written by lc init and lc guide
  CLAUDE.md             imports AGENTS.md, for Claude Code
```

Splitting comps and styles into their own files keeps an agent's edit small and its diff
readable, and lets two edits to different comps never conflict. Media records get one
file each for the same reason, and because `lc import` writes them while the person and
the agent are editing comps: an import never rewrites a file someone else is editing
([01-architecture.md](01-architecture.md#files-edits-and-concurrency)).

## Time

### The unit

Internally every time is an integer count of ticks at 705,600,000 per second (the
"flick"). It divides exactly into a frame at every rate the tool meets and a sample at
every audio rate, checked by computation:

| rate | ticks per frame or sample |
| --- | --- |
| 23.976 (24000/1001) | 29,429,400 |
| 29.97 (30000/1001) | 23,543,520 |
| 59.94 (60000/1001) | 11,771,760 |
| 119.88 (120000/1001) | 5,885,880 |
| 24, 25, 30, 48, 50, 60, 100, 120, 240 | all exact |
| 44.1, 48, 96 kHz | 16,000, 14,700, 7,350 |

The frame rates in this table are the supported rates; a source at any other rate is an
error at import. The audio rows are about divisibility only: there is no literal for audio
samples. Rates are written as ffprobe writes them, a rational such as `30000/1001`, or an
integer (`60/1` is written back as `60`). A signed 64-bit count lasts 414 years. Mixed-rate timelines (29.97 sources, a 120p slow
motion clip, a 60p comp) therefore never accumulate rounding.

### Time in files

Times are written as strings with a unit, because the unit is part of what the author
meant:

| literal | meaning |
| --- | --- |
| `"f:360"` | frame 360 at the comp's rate (at the media's rate in a source-time field) |
| `"12.5s"` | seconds |
| `"b:64"`, `"b:64+0.5"` | beat 64 of the comp's beat map, plus half a beat |
| `"bar:17"` | downbeat 17 |
| `"tc:00:00:12:15"` | timecode at the comp's rate |

A value keeps the unit it was written in: when the UI moves a keyframe that was placed
at `"b:4"`, it writes back a beat literal, so a beat-anchored edit stays beat-anchored
when the music is re-analysed (within the rule in [beat maps](#beat-maps)) or the tempo
map is nudged.

### Literal grammar

- **Frames and beats count from 0.** `f:0` is the comp's first frame (for a clip-level
  command such as `lc still --media`, the clip's first frame at the clip's own rate);
  `b:0` is the first beat of the comp's beat map ([beat maps](#beat-maps)), which need not
  be at `0s` (music can start with a pickup). `bar:0` is the first downbeat.
- **Seconds** are decimal with at most five fractional digits, because 705,600,000 is
  `2^9 * 3^2 * 5^5 * 7^2`: five decimal places are always exact in ticks. More digits, or a
  value that is not exact, is an error, never rounded.
- **Indices are integers.** In a position, `b:64` and `f:120` are legal and `b:64.5` is
  not; fractions are written as offsets. A span is a count, not an index, so a beat span
  may be a decimal (`b:4.5`, or `b:-0.25` in a style) and `b:4+0.5` is written back as
  `b:4.5`. Frames are whole in both.
- **Offsets** add to a base: `b:64+0.5`, `b:64-0.25`, `f:120+3`. A fractional beat offset is a
  fraction of the beat interval it falls in: `b:64+0.5` is halfway between beats 64 and 65,
  `b:64-0.25` is a quarter of the way back from beat 64 towards beat 63. A frame offset
  folds into its base when formatted (`f:120+3` becomes `f:123`); a beat offset stays, since
  its fraction is not a beat index. An offset of more than a beat carries on into the next
  intervals (`b:64+1.25` is the same time as `b:65+0.25`). Frame offsets are whole frames,
  and a bar takes no offset.
- **Signs.** Negative values are allowed only in literals relative to a style's anchor
  (`b:-2`). A position in a comp, and a span in a layer's own time, is at least 0.
- **Timecode** `tc:HH:MM:SS:FF` is non-drop-frame frame counting at the comp's rate,
  rounded up (30 frames a second at 29.97). It names comp positions only, and its frames
  field has two digits, so it is defined up to 100 fps. Drop-frame timecode is not
  supported, and a drop-frame literal is an error.
- **Positions and spans.** Every field is declared as one or the other in the schema. A
  position literal names a point in comp time; a span literal names a length, measured
  from the field's anchor (a beat span is the time between the anchor and the anchor plus
  that many beats on the beat map; an anchor between two beats counts from its fractional
  place on the grid, so `b:4` from 2.3 beats ends at 6.3 beats). A layer's `start` is a
  position, and every other time inside a layer is a span from it
  ([layer timing](#layer-timing)).
- **Source time.** Fields that name a time in a media file (`source_start`, the values of
  remap keys) take seconds or `f:`, and there `f:N` counts frames at the media's own rate.
  Beat literals are an error in them, because a beat map belongs to a comp. Source time 0
  is the first frame the decoder presents, not the file's timecode: the S-Log3 sample
  starts at timecode 00:00:21:15 (its data stream tag and its XML agree), and source time
  0 is that frame. The start timecode is kept in the media record for interchange
  ([02-media.md](02-media.md#rates-and-timecode)).
- **Round trip.** `format(parse(x))` returns the canonical spelling of `x` (no leading
  zeros, offsets reduced to one, `+0` dropped), and `parse(format(t, unit)) == t` for every
  tick value exactly representable in that unit. Tests cover every rate in the table
  above.

### From time to frames and samples

A literal resolves to an exact tick count, and parsing never rounds. Rounding happens only
where a time has to land on a grid, by one stated rule for each grid:

- **Cuts land on the first output frame at or after their time.** A layer's visible in and
  out points and a comp's `end` are resolved to ticks, then moved to the first frame
  boundary at or after that time. Beats rarely fall on frames (at 128 BPM and 60 fps a beat
  is 28.125 frames), so a picture cut lands up to one frame after its beat, which is the
  rule past edits used. *(proposed)*
- **Keyframes are not moved.** A curve is evaluated at each output frame's time; a key
  between two frames shapes the curve where it is.
- **A beat literal lands on the nearest tick**, halves up. Beats fall on no grid of their
  own, so a fraction of a beat interval is rarely a whole number of ticks; a tick is
  1.4 ns. Written back from ticks, a beat literal takes the shortest decimal that resolves
  to the same tick, so `b:12+0.25` stays `b:12+0.25`.
- **Audio lands on the nearest sample of the mix rate**, halves rounding up. At the
  1000/1001 rates a frame boundary is not a sample boundary (29.97p has 1601.6 samples
  per frame at 48 kHz), so an audio layer's in and out points, fades and SFX anchors
  follow their own time to the sample, not the picture's frame. An audio layer linked to
  footage takes the footage's frame-resolved in and out instead, so picture and its own
  sound cut together. *(proposed)*
- The FCPXML exporter (Phase 0) writes the frame-resolved cut times.
- **A source time between two frames shows the frame whose interval holds it**, the
  frame on screen at that moment. In source time `f:N` is frame N; a footage layer's comp
  frame shows the source frame that holds `source_start + speed * (frame time - start)`,
  and `lc still --media --at 12.5s` the frame that holds 12.5 s. FCPXML's time maps sample
  the same way by default (`frameSampling="floor"`, its 1.10 DTD). *(proposed)*
- **Markers land on the first frame at or after their time**, like cuts, since FCPXML and
  Resolve put a marker on a frame. *(proposed)*

### Phase 0's part of the comp format

`lc fcpxml`, `lc render` and `lc validate` read comps with `footage` and `audio` layers
(`start`, `in`, `out`, `source_start`, `markers`, and for footage `time: { "speed": ... }`),
`graphics` layers (`page`, a page in `graphics/` keeping the past pages' contract, with
`space: "display"`, `params`, `start`, `in`, `out`, `markers`), comp `markers`,
`beats: { "from_layers": [...] }`, `size`, `rate` and `end` (`schema/comp.schema.json`).
Footage layers also take `transform`: `position` (fractions of the comp's width and
height, where the layer's anchor lands; default the middle), `anchor` (fractions of the
layer's frame as shown; default its middle), `scale` (relative to the layer's own pixels,
one number or x and y; default 1), `rotation` (degrees, clockwise on screen about the
anchor, as in After Effects; the layer is scaled with the exact weights first, then
sampled bilinearly through the turn, D15) and `opacity` (0 to 1), each a constant or
`{ "keys": [ { "t": <layer time>, "v": ..., "ease": "in" | "out" | "in_out" | "hold" } ] }`
(*proposed* units). A key's `ease` shapes the segment arriving at it in the names' CSS
sense (`out` slows into the key, `in` starts slowly from the one before), an eased end
having After Effects' Easy Ease (speed 0, influence a third of the segment): the example
above eases its last key `out`, which only that reading gives a meaning (*proposed*,
`src/lungecuta/keyframes.py`). A layer without a transform sits at its own size in the
middle of the comp, as in After Effects. Footage layers also take `grade`, M1's part of
the clip grade: a list of operations applied in order to the layer's frame in the
working space, after its input transform and before it is placed. `primary` is OCIO's
primary grade in its LOG style with its controls in working-space code values (`offset`,
`contrast`, `pivot`, default the working space's 18 % grey, `gamma`, `saturation`,
`clamp`); `cdl` is an ASC CDL (`slope`, `offset`, `power`, `saturation`, `clamp`); `lut`
is a file under `luts/` with the colour spaces it expects (`space`) and returns (`out`)
declared ([04-color.md](04-color.md#operations-come-from-opencolorio)); or a node graph,
`{ "nodes": [ { "ops": [...], "window": [...], "mix": 1 } ] }`
([04-color.md](04-color.md#node-graph)). The example's separate file
(`grades/shot3.json`) and qualifier keys come later in M4.
Footage layers also take `motion_blur`, `{ "shutter_deg": 180 }` (degrees of a frame,
0 to 720, a constant or keys), the layer's own movement averaged over that shutter
([06-editing.md](06-editing.md#motion-blur)).
Footage layers also take `masks`, in order, each a `rectangle` or `ellipse` in the layer's
own frame as shown (`centre` and `size` in fractions of its width and height, as After
Effects' masks are in layer space), its edge ramped linearly over `feather` (a fraction
of the layer's height, centred on the edge, at least a pixel), optionally `invert`ed, and
combined with the masks before it by `mode`: `add` (a + b - ab), `subtract` (a (1 - b)) or
`intersect` (a b), starting from nothing (D15: the ramp and the formulas follow the
names, not a measurement of After Effects). The mask is the layer's own alpha, resampled
with its colour through scale and rotation (`src/lungecuta/geometry.py`); `lc op masks`
replaces the list, and FCPXML refuses masks. A `comp` layer places another comp (`comp`: its id, listed in `project.json`) with
`start`, `in`, `out`, `transform`, `masks` and `markers`: comp frame f shows the precomp's
frame whose interval holds layer time `f - start` (the source-time rule, D12, *proposed*), the
precomp composited in the working space with its coverage, transparent where it has
nothing, and placed and masked as footage is. A comp that contains itself is refused. A
precomp's sound is its own mix, through its layers' gains and fades, played where its
layer is visible, its layer time the precomp's time (as in After Effects), each end on
the nearest sample; a render's manifest lists the precomps and their media, so a
change to one makes the render and the preview stale. A precomp's graphics enter the
working space through the inverse output view, since inside a precomp they are never the
top of the final stack (D15); the editor draws a precomp's footage but not its
graphics, renders read its footage a frame at a time (no streaming decode), and FCPXML
refuses precomp layers. A comp's `transitions` lists dissolves, dips and fades on its footage layers
([06-editing.md](06-editing.md#transitions)). Any other field is a validation error rather
than something ignored, until M0 adds it. Layers are listed in stack order, **a later
layer drawn over an earlier one** (the order of the example above, *proposed*), and
composite in the working space with premultiplied alpha. Footage layers may overlap in a
render; the FCPXML export writes one picture track and refuses overlaps, transforms and grades;
graphics layers in the top run of the stack are drawn after the output view on
display-encoded values, and one under footage is brought into the working space through
the inverse of the output view at its place in the stack
([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)); the editor
does not draw one under footage yet, and FCPXML refuses it; audio layers are music records, and an audio layer's
`source_start` is in seconds, since audio has no frames. Audio layers take `gain` in dB
(a constant or keys in layer time, evaluated on every sample) and `fade_in` and
`fade_out`, spans from the visible in point and back from the visible out point (beats
counted on the grid), over which the layer rises from and falls to silence linearly in
amplitude (D15). FCPXML carries a constant gain as the
DTD's `adjust-volume amount`; it refuses gain keys and fades until the parameter that
animates the amount is read from Apple's reference. A graphics layer's page sees its
`params` as JSON in its `params` query parameter and is asked for layer time `t`. A comp
is listed in `project.json` and lives at `comps/ID.json`.

## project.json

```json
{
  "format": 1,
  "color": {
    "config": "studio-config-v5.0.0_aces-v2.1_ocio-v2.6",
    "working": "DaVinci Intermediate WideGamut",
    "linear": "Linear DaVinci WideGamut",
    "display": "Rec.1886 Rec.709 - Display",
    "view": "ACES 2.0 - SDR 100 nits (Rec.709)"
  },
  "media_roots": ["footage", "music"],
  "comps": ["comps/main.json", "comps/title-card.json"],
  "proxy_height": 540,
  "deliver": ["web-hevc10", "master-prores-hq"]
}
```

An optional `lint` holds `lc lint`'s thresholds (`graphics_min_seconds`, 0.6 by default;
`repeated_source` and `music_licence`, on by default), from the past projects' review
rules ([09-agent-interface.md](09-agent-interface.md#lint-rules-learned-the-hard-way)).

### media

One file per media key, `media/KEY.json`, with an optional `licence` (`name`, `source`,
`checked`, `note`) that a person records with `lc licence` after checking the source's
own page. A decided record, as `lc import` writes it for
the S-Log3 sample clip into `media/C0001.json`. The values were measured on that file,
except its name (here `C0001`) and `creation_time`, which are placeholders, and `hash`
and `stat`, which are left out:

```json
{
  "key": "C0001",
  "path": { "root": "footage", "rel": "C0001.MP4" },
  "hash": "sha256:...",
  "stat": { "size": "...", "mtime_ns": "..." },
  "probe": {
    "video": { "codec": "h264", "profile": "High 4:2:2", "pix_fmt": "yuv422p10le",
               "size": [3840, 2160], "rate": "30000/1001", "frames": 705,
               "tags": { "color_range": "pc", "color_space": "unknown",
                         "color_transfer": "unknown", "color_primaries": "unknown" },
               "chroma_location": "left", "rotation": 0 },
    "audio": [ { "codec": "pcm_s24be", "channels": 2, "layout": "stereo", "rate": 48000 } ],
    "data_streams": 1,
    "timecode": "00:00:21:15",
    "creation_time": "2026-01-01T00:00:00.000000Z"
  },
  "time": {
    "status": "decided", "rate": "30000/1001", "capture_rate": "30000/1001", "start_tc": "00:00:21:15",
    "evidence": { "source": "sony-xml", "file": "C0001M01.XML",
                  "captureFps": "29.97p", "formatFps": "29.97p",
                  "RecordingMode": "normal", "LtcChange": "55210000" }
  },
  "color": {
    "status": "decided",
    "space": "S-Log3 S-Gamut3.Cine",
    "matrix": "bt709",
    "range": "full",
    "evidence": { "source": "sony-xml", "file": "C0001M01.XML",
                  "CaptureGammaEquation": "s-log3-cine",
                  "CaptureColorPrimaries": "s-gamut3-cine",
                  "CodingEquations": "rec709",
                  "stream_tags": { "color_range": "pc", "color_space": "unknown",
                                   "color_transfer": "unknown", "color_primaries": "unknown" } }
  },
  "decode": { "decoder": "software",
              "checked": { "method": "framemd5", "frames": 300, "against": "videotoolbox",
                           "result": "failed",
                           "detail": "300 of 300 frames; videotoolbox reported: h264: hardware accelerator failed to decode picture" } },
  "proxy": { "path": "cache/proxy/C0001.mkv", "profile": "ffv1-444", "pix_fmt": "yuv444p10le",
             "size": [960, 540], "matrix": "bt709", "range": "full",
             "parity": { "frames": [0, 70, 140, 211, 281, 352, 422, 492, 563, 633, 704],
                         "codes": { "p99": 1.2381, "max": 1.4278,
                                    "bias": [0.0669, 0.0173, 0.0746], "p99_blocks": 0.3906 },
                         "di": { "p99": 3.9393, "max": 16.3697,
                                 "bias": [0.51, 0.5629, 0.6167], "p99_blocks": 2.3884 },
                         "reason": null } },
  "derived_from": null
}
```

An undecided record, as for the HLG sample (here `C0002`): `color` keeps the evidence and the
reason, and has no `space`:

```json
"color": {
  "status": "undecided",
  "reason": "no mapping for sony-xml hlg-slog3ootf + rec2020",
  "evidence": { "source": "sony-xml", "file": "C0002M01.XML",
                "CaptureGammaEquation": "hlg-slog3ootf",
                "CaptureColorPrimaries": "rec2020",
                "CodingEquations": "rec2020ncl",
                "stream_tags": { "color_space": "bt2020nc", "color_transfer": "arib-std-b67",
                                 "color_primaries": "bt2020", "color_range": "tv" } }
}
```

- Media keys default to the file stem (`C0001`); a second file with the same
  stem is an error until one of them is given an explicit key.
- **Paths are relative to a named media root.** `project.json` lists the root names the
  project uses; `roots.local.json`, gitignored and written empty by `lc init`, says where
  each root is on this machine (`{ "footage": "/path/to/shoots" }`). The root `project` is
  the project directory itself. A committed project therefore holds no absolute path and
  opens on another machine once its roots are set. A file not found under its root is
  missing, and `lc relink --root NAME DIR` looks for it under a new directory by size and
  then hash; nothing is matched by name alone.
- **The hash is computed at import.** On open, a file whose size and modification time
  match `stat` is taken as unchanged; otherwise it is hashed again, and a different hash
  makes its decode check, proxy and colour decision stale, so the clip is undecided until
  it is imported again.
- A clip undecided in colour or in time cannot be placed in a comp, by design
  ([02-media.md](02-media.md#colour-space-detection),
  [rates and timecode](02-media.md#rates-and-timecode)).
- The proxy has its own decode record (its format, range and matrix,
  [02-media.md](02-media.md#proxies)); its curve and gamut are the original's. Its `parity`
  entry holds the measured proxy-against-original difference, in the file's own codes (the
  gate) and in DaVinci Intermediate. The proxy format above is provisional.
- `stream_tags` are ffprobe's names, with `unknown` where the stream carries no tag (as
  ffprobe's flat output prints it; its JSON leaves the key out).
- **Rotation** is recorded from the probe (the HLG samples carry 90) and applied in the
  geometry stage of the render, never baked into the proxy: proxies keep the coded
  orientation, and `proxy_height` refers to the coded frame. Whether a rotation tag is
  right is judged by looking at a still; a wrong tag is overridden in the record with a
  note, since some Sony files carry wrong tags ([06-editing.md](06-editing.md#framing)).
  The tag is FFmpeg's display-matrix angle, counterclockwise: `lc still` turns a frame
  tagged 90 a quarter turn counterclockwise, checked against ffmpeg's autorotate at 90,
  180 and -90 on synthetic clips and at 90 on the HLG sample. A display matrix that
  mirrors the picture is refused at import, since only rotations are recorded.

### beat maps

A beat map belongs to a music media record, in its file under `media/`, next to the WAV
it was found in. A music record (a file with no video stream) has `wav` and `beats` where
footage has `time`, `color`, `decode` and `proxy`, which are `null`:

```json
"wav": { "path": "cache/audio/track01.wav", "rate": 48000, "channels": 2, "format": "pcm_f32le",
         "samples": 5992968, "hash": "sha256:...", "peak": 0.804137, "resampled_from": null },
"beats": {
  "analyzer": { "name": "beat_this", "version": "1.1.0", "checkpoint": "final0",
                "checkpoint_hash": "sha256:...", "postprocessing": "minimal", "device": "cpu" },
  "times": [0.512, 1.112, 1.712],
  "downbeats": [0, 4, 8],
  "manual": [ { "index": 2, "time": 1.705 } ]
}
```

- `times` are seconds from the first sample of the WAV, one per beat, in order;
  `downbeats` are indices into `times`; `manual` lists beats moved by hand, which
  re-analysis keeps, each with the time `times` holds for it.
- The WAV is in the cache, so it is not committed: when it is missing it is decoded again
  from the original, and it must come out with the recorded hash, or the record is stale.
- **Re-analysis keeps every index or is not applied.** A new analysis (another analyzer
  version, or a request to run it again) is matched to the current map beat by beat, each
  new beat to the nearest old one within half the median beat interval. If every old beat
  has exactly one match, the new times replace the old ones and every literal keeps its
  musical beat. If a beat is gained or lost (typically in a pickup or a soft intro), every
  index after it would shift and silently move every beat literal, so the new map is not
  applied: the command lists the inserted and removed beats and each literal in the
  project that would then name a different beat, and the person decides. `manual` beats
  are carried over by the same matching. *(proposed)* A new map whose downbeats fall on
  other indices is not applied either, since every `bar:` literal would move (*proposed*,
  added with `lc beats`). `lc beats KEY` runs the analysis again; `--replace` applies a
  map that shifts indices once the person has decided to.
- **A comp's grid comes from its music layers**: `beats: { "from_layers": ["music"] }`.
  Each named audio layer contributes the beats of its media's map that fall inside its
  visible range, beat `k` at comp time `layer.start + (times[k] - layer.source_start)`
  (music layers play at 100 %). Where two of them overlap (a crossfade), the beats in the
  overlap come from the later layer. *(proposed)* The contributions are numbered in comp
  order, so `b:0` is the first beat of the comp's grid, which need not be at `0s`, and a
  track cut on bars into several layers ([07-audio.md](07-audio.md#music-editing)) still
  gives one grid. Downbeats are numbered the same way for `bar:`.
- A beat literal names a place on the comp's grid, not a moment of the song: slipping a
  music layer moves every beat literal onto other music. The fields of a music layer that
  the grid depends on (`start`, `in`, `out`, `source_start`) cannot be beat literals.
- The tempo shown in the UI is derived from the mean beat interval; it is never an input.

## Compositions and layers

The After Effects model: a comp has a size, a rate, an `end` (a position; every comp
starts at `0s`), an optional beat grid (from its music layers) and an ordered list of
layers. A timeline is a comp; a precomp is a comp used as a layer.

```json
{
  "id": "main",
  "size": [1080, 1920],
  "rate": "30000/1001",
  "end": "b:128",
  "beats": { "from_layers": ["music"] },
  "layers": [
    { "id": "music", "type": "audio", "media": "track01",
      "start": "0s", "out": "61.2s", "source_start": "0s" },
    {
      "id": "shot3",
      "type": "footage",
      "media": "C0001",
      "start": "b:60", "out": "b:8",
      "source_start": "8.2s",
      "time": { "style": "drop-ramp", "at": "b:4", "overrides": { "intensity": 0.7 } },
      "retime": "flow",
      "transform": {
        "scale": { "keys": [ { "t": "b:4", "v": 1.0 },
                             { "t": "b:4.5", "v": 1.08, "ease": "out" } ] },
        "position": { "track": "shot3-lock" }
      },
      "grade": "grades/shot3.json",
      "motion_blur": { "shutter_deg": 180 }
    },
    { "id": "title", "type": "graphics", "code": "graphics/lower-third.js",
      "space": "display", "start": "b:64", "out": "b:8",
      "params": { "text": "Event name 2026" } }
  ]
}
```

`shot3` starts at comp beat 60 and runs 8 beats; its zoom keys and its ramp sit 4 beats
in, on comp beat 64, the drop. The title starts on the same beat.

### Layer timing

The After Effects model, chosen by the user (D11):

- `start` is the comp position of the layer's own time 0. It is the only comp position in
  a layer.
- `in` and `out` are spans from `start`: the layer is visible from `start + in` to
  `start + out`, resolved to frames as in [above](#from-time-to-frames-and-samples). `in`
  defaults to 0, so an untrimmed layer begins at `start`.
- **Every other time of a layer is layer time**, a span from `start`: keyframes, time-remap
  keys, a style placement's `at`, layer markers, and the `t` a graphics layer is rendered
  at ([05-motion-graphics.md](05-motion-graphics.md#the-contract-every-past-page-already-met)).
- A footage or audio layer whose time is given as speed (absent, `speed`, or a style) has a
  `source_start`, the source time at layer time 0. A layer with explicit `remap` keys has
  none, since its keys give source time directly.
- A precomp layer's layer time is the precomp's own time, as in After Effects. A precomp's
  beat grid is its own, from its own music layers, and it does not see its parent's
  (D15); a beat literal in a precomp without music is an error.

Each edit changes these fields, and keyframes follow from that:

| edit | fields changed | keyframes, seen in the comp |
| --- | --- | --- |
| move (drag the layer) | `start` | move with the layer |
| trim the in or out point | `in` or `out` | stay where they are |
| slip (move the content under fixed in and out points) | `source_start`, or every remap key's source value by the same amount | stay where they are |
| slide, roll, ripple | `start`, `in` and `out` of the layers involved ([06-editing.md](06-editing.md#edit-operations)) | follow from the rows above |

Slip leaving keyframes in place is After Effects' behaviour unless the keyframes are
selected first: slip editing moves "the content of a layer without moving its keyframes
or in and out points in relation to the composition" (Chris and Trish Meyer, *Creating
Motion Graphics with After Effects*, 5th ed., ch. 7, as excerpted by ProVideo Coalition;
a secondary source, Adobe's own help page could not be fetched).

Beat spans keep keyframes on the grid when a layer moves: with `start` at `b:60`, a key at
`b:4` sits on comp beat 64, and moving the layer to `b:61` puts it on beat 65, still on a
beat where the tempo changes.

### Layer types

| type | content |
| --- | --- |
| `footage` | a media record, with source start, time remap, `retime` (`nearest`, `blend`, `flow`), transform, grade |
| `still` | an image; same transform and grade as footage |
| `audio` | a media record or a generated sound; gain, fades, effects ([07-audio.md](07-audio.md)) |
| `solid` | a colour |
| `shape` | vector paths, fills, strokes, trim paths |
| `text` | text with styling and per-character animators |
| `graphics` | code-driven graphics ([05-motion-graphics.md](05-motion-graphics.md)) |
| `comp` | a precomp |
| `adjustment` | applies its effects and grade to everything below it |

Every visual layer has: `start`, `in`/`out` ([layer timing](#layer-timing)), `transform` (anchor, position, scale, rotation,
opacity), `parent`, `masks`, `effects` (an ordered stack), `blend` mode, `matte` (track
matte from another layer), `motion_blur`, and `space` (`scene` or `display`, see
[04-color.md](04-color.md#graphics)).

### Properties, keyframes, easing, expressions

A property is either a constant, a keyframe list, an expression, or a reference to a
tracker's output:

```json
"opacity": 0.8
"opacity": { "keys": [ { "t": "b:12", "v": 0 }, { "t": "b:13", "v": 1, "ease": "in_out" } ] }
"rotation": { "expr": "wiggle(2, 4)" }
"position": { "track": "shot3-lock" }
```

A key's `t` is layer time ([layer timing](#layer-timing)).

Interpolation follows After Effects, because that is the model the graph editor edits:

- per keyframe, `in` and `out` interpolation: `linear`, `bezier`, `hold`;
- bezier tangents as speed and influence (After Effects' terms), so the speed graph and the
  value graph are two views of the same data;
- named eases (`in`, `out`, `in_out`, `snap`) are shorthands that expand to specific speed
  and influence values, written out explicitly the moment someone edits them in the graph
  editor, so the file never hides a curve behind a name that changed meaning.

Expressions are JavaScript, evaluated in the sandboxed expression runtime with a small
fixed API (`time`, `beat`, `value`, `wiggle`, `ease`, `linear`, `loopOut`, other layers'
properties, audio amplitude of a layer). They are deterministic: `wiggle` and `random`
are seeded by layer and property, never by wall-clock time.

### Time remap

The canonical curve is **source time against layer time**, After Effects' time remap,
stored as keyframes in `time.remap`, each `{ "t": <layer time>, "v": <source time> }`.
Speed is its derivative, and the speed graph is a view of it, not a second store. A
footage layer's `time` is one of:

- absent: plays at 100 % from `source_start`;
- `{ "speed": 2.0 }`: constant speed from `source_start`, shorthand for a straight remap line;
- `{ "remap": [...] }`: source-time keyframes;
- `{ "style": "...", "at": "...", "overrides": {...} }`: a style instance, `at` in layer
  time, speed 1 from `source_start` outside the style's tracks.

As written so far (`src/lungecuta/comp.py`): `time: { "remap": [ { "t": <layer time>,
"v": <source time>, "ease": ... } ] }` (at least two keys; a layer with them has no
`source_start`) evaluates between keys with the same easing as every other property
([keyframes](#properties-keyframes-easing-expressions)), to the tick; a remap may run
backwards, and a render then reads the layer by a seek per frame instead of one pass.
`speed`, `remap` and style instances are written. A style's speed track is integrated
exactly, not to a tolerance: with Easy Ease's influence of a third, a segment's time
curve is linear in the bezier's parameter, so speed is a cubic in time and its integral a
quartic (`Track.integral`, `src/lungecuta/keyframes.py`). The compiled source time is
computed from the curve when the comp is resolved rather than stored, so nothing derived
is written into the file and nothing needs compiling again by hand; detaching an
instance into keys is not written yet.

A style or a speed graph edit describes speed; it is compiled into remap keys when it is
placed. For a linked style instance the compiled keys are derived data: they live in the
cache and are compiled again when the style, its overrides or the beat grid change, and
detaching the instance writes them into the file. Between two keys the source position is

```
source(t) = source(t_k) + ∫ speed(u) du   from t_k to t
```

integrated exactly for constant, linear and eased speed segments alike (an eased
segment's integral is a quartic in time, as written below). Evaluating a frame then needs only the remap curve, so a random seek costs the
same as any other, with no integration from the in point. A placement that needs more
source than the clip has is an error naming both durations.

## Styles

A style is a reusable asset: a set of property tracks, written in **relative units** so
that it fits wherever it is placed.

```json
{
  "id": "drop-ramp",
  "name": "accelerate into the drop, slow motion on it",
  "anchor": "beat",
  "params": { "intensity": { "default": 1.0, "min": 0, "max": 2 } },
  "tracks": {
    "speed": [ { "at": "b:-2",    "v": 1.0,  "ease": "in" },
               { "at": "b:-0.25", "v": 3.0,  "ease": "out" },
               { "at": "b:0",     "v": 0.25, "ease": "in_out" },
               { "at": "b:2",     "v": 1.0 } ],
    "scale": [ { "at": "b:0", "v": 1.08, "ease": "out" }, { "at": "b:0.5", "v": 1.0 } ],
    "shutter_deg": [ { "at": "b:-0.5", "v": 0 }, { "at": "b:-0.25", "v": 180 },
                     { "at": "b:0", "v": 0 } ]
  }
}
```

- Times in a style are relative to its anchor (`b:-2` is two beats before the placement).
- **Instances are linked.** Editing the style changes every placement; an instance can
  override parameters, and can be detached into an independent copy.
- `intensity` works like Pickapicka's preset Amount: it moves each value from neutral
  towards the style's value, so 0 is no effect and 1 is the style as written.
- Applying the same style again at the same anchor replaces the earlier instance rather
  than stacking a second one (the rule Pickapicka's presets settled on).
- Every track in a style is an ordinary layer property (here `shutter_deg` is the layer's
  motion-blur shutter, keyframed), so placing a style writes nothing the graph editor
  cannot show.
- A look is a style whose tracks are grade nodes and LUT references; each LUT reference
  names its input and output colour spaces, because a `.cube` file declares neither.
  Linking gives what Resolve gets from shared nodes and colour groups. Written: a style's
  `grade` (with `tracks` optional, and one of the two required), used by a clip's grade
  node `{ "look": ID }` ([04-color.md](04-color.md#node-graph)); its grade is static, not
  yet keyed in time.
- A style can only use effects the engine already has. When a style needs a new kind of
  effect, that effect is added to the engine as code with a test, and the style refers to
  it by name. Styles stay data the UI can edit.

### M2's part of styles

`styles/ID.json` (`schema/style.schema.json`) with `anchor: "beat"`, an optional
`intensity` parameter, and two tracks: `speed` (source seconds per layer second, at least
0, 1 outside the track) and `scale` (a multiplier on the layer's scale, 1 outside the
track, so a track that starts away from 1 jumps there, the punch-in of the example). A
footage layer places one with `time: { "style", "at", "overrides" }`, `at` a beat span
from the layer's start; `lc op style COMP LAYER STYLE --at <comp beat>` writes it,
replacing the layer's earlier instance, and `lc op unstyle` takes it off. Every value is
moved from 1 towards the style's by the intensity, `1 + intensity (v - 1)`, and a speed
that would go below 0 or a scale to 0 or below is an error naming the intensity. Keys may
fall before the layer's start, and a key before the beat grid's first beat is an error.
The editor's cached frames are keyed by the comp and the styles and LUTs it uses, so
editing a style changes what every placement shows. The shutter track waits for motion
blur (M6), and is a validation error until then.

## Validation

`lc validate` checks a project against the schema and then against meaning: every
reference resolves, every clip has a decided colour space, every placement has enough
source, every beat literal falls inside the beat grid, no source-time field and no field
the grid depends on is a beat literal, every layer has `in` at most `out`, a layer with
`remap` keys has no `source_start`, every expression parses. The UI runs the same check
on load and after every external change.
