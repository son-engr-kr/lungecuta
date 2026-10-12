# Project format

A project is a directory. Every file in it is JSON that a person can read and an agent
can write, validated against a schema generated from the engine's pydantic models and
committed as `schema/*.json`.

```
myedit.lc/
  project.json          settings, colour management, media records, comp index
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
  cache/                proxies, rendered frames, analysis; never committed
```

Splitting comps and styles into their own files keeps an agent's edit small and its diff
readable, and lets two edits to different comps never conflict.

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
samples. A signed 64-bit count lasts 414 years. Mixed-rate timelines (29.97 sources, a 120p slow
motion clip, a 60p comp) therefore never accumulate rounding.

### Time in files

Times are written as strings with a unit, because the unit is part of what the author
meant:

| literal | meaning |
| --- | --- |
| `"f:360"` | frame 360 at the comp's rate |
| `"12.5s"` | seconds |
| `"b:64"`, `"b:64+0.5"` | beat 64 of the comp's beat map, plus half a beat |
| `"bar:17"` | downbeat 17 |
| `"tc:00:00:12:15"` | timecode at the comp's rate |

A value keeps the unit it was written in: when the UI moves a keyframe that was placed
at `"b:64"`, it writes back a beat literal, so a beat-anchored edit stays beat-anchored
when the music is re-analysed or the tempo map is nudged.

### Literal grammar

- **Frames and beats count from 0.** `f:0` is the comp's first frame (for a clip-level
  command such as `lc still --media`, the clip's first frame at the clip's own rate);
  `b:0` is the first beat of the comp's beat map ([beat maps](#beat-maps)), which need not
  be at `0s` (music can start with a pickup). `bar:0` is the first downbeat.
- **Seconds** are decimal with at most five fractional digits, because 705,600,000 is
  `2^9 * 3^2 * 5^5 * 7^2`: five decimal places are always exact in ticks. More digits, or a
  value that is not exact, is an error, never rounded.
- **Indices are integers.** `b:64` and `f:120` are legal, `b:64.5` is not; fractions are
  written as offsets.
- **Offsets** add to a base: `b:64+0.5`, `b:64-0.25`, `f:120+3`. A fractional beat offset is a
  fraction of the beat interval it falls in: `b:64+0.5` is halfway between beats 64 and 65,
  `b:64-0.25` is a quarter of the way back from beat 64 towards beat 63. A frame offset
  folds into its base when formatted (`f:120+3` becomes `f:123`); a beat offset stays, since
  its fraction is not a beat index.
- **Signs.** Negative values are allowed only in literals relative to an anchor (styles:
  `b:-2`). An absolute position in a comp is at least 0.
- **Timecode** `tc:HH:MM:SS:FF` is non-drop-frame frame counting at the comp's rate.
  Drop-frame timecode is not supported, and a drop-frame literal is an error.
- **Positions and spans.** Every field is declared as one or the other in the schema. A
  position literal names a point in time; a span literal names a length, measured from
  the field's anchor (a beat span is the time between the anchor and the anchor plus that
  many beats on the beat map).
- **Round trip.** `format(parse(x))` returns the canonical spelling of `x` (no leading
  zeros, offsets reduced to one, `+0` dropped), and `parse(format(t, unit)) == t` for every
  tick value exactly representable in that unit. Tests cover every rate in the table
  above.

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
  "media": { "...": "see below" },
  "comps": ["comps/main.json", "comps/title-card.json"],
  "proxy_height": 540,
  "deliver": ["web-hevc10", "master-prores-hq"]
}
```

### media

A decided record, as `lc import` writes it for the S-Log3 sample clip. The values were
measured on that file, except its name (here `C0001`) and `creation_time`, which are
placeholders; `path` and `hash` are per machine:

```json
"C0001": {
  "path": "/path/to/C0001.MP4",
  "hash": "sha256:...",
  "probe": {
    "video": { "codec": "h264", "profile": "High 4:2:2", "pix_fmt": "yuv422p10le",
               "size": [3840, 2160], "rate": "30000/1001", "frames": 705,
               "tags": { "color_range": "pc", "color_space": "unknown",
                         "color_transfer": "unknown", "color_primaries": "unknown" },
               "rotation": 0 },
    "audio": [ { "codec": "pcm_s24be", "channels": 2, "layout": "stereo", "rate": 48000 } ],
    "data_streams": 1,
    "creation_time": "2026-01-01T00:00:00.000000Z"
  },
  "color": {
    "status": "decided",
    "space": "S-Log3 S-Gamut3.Cine",
    "matrix": "bt709",
    "range": "full",
    "evidence": { "source": "sony-xml", "file": "C0001M01.XML",
                  "CaptureGammaEquation": "s-log3-cine",
                  "CaptureColorPrimaries": "s-gamut3-cine",
                  "CodingEquations": "rec709" }
  },
  "decode": { "decoder": "software",
              "checked": { "method": "framemd5", "frames": 300,
                           "against": "videotoolbox", "result": "videotoolbox differs" } },
  "proxy": { "path": "cache/proxy/C0001.mov", "size": [960, 540],
             "matrix": "bt709", "range": "limited",
             "parity": { "metric": "DI 10-bit codes, p99", "value": null } },
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
- An undecided clip cannot be placed in a comp, by design
  ([02-media.md](02-media.md#colour-space-detection)).
- The proxy has its own decode record, because it is written in limited range whatever the
  original's range is ([02-media.md](02-media.md#proxies)); its curve and gamut are the
  original's. Its `parity` entry holds the measured proxy-against-original difference.
- **Rotation** is recorded from the probe (the HLG samples carry 90) and applied in the
  geometry stage of the render, never baked into the proxy: proxies keep the coded
  orientation, and `proxy_height` refers to the coded frame. Whether a rotation tag is
  right is judged by looking at a still; a wrong tag is overridden in the record with a
  note, since some Sony files carry wrong tags ([06-editing.md](06-editing.md#framing)).

### beat maps

A beat map belongs to a music media record:

```json
"beats": {
  "analyzer": { "name": "beat_this", "version": "..." },
  "times": [0.512, 1.112, 1.712],
  "downbeats": [0, 4, 8],
  "manual": [ { "index": 2, "time": 1.705 } ]
}
```

- `times` are seconds from the start of the music file, one per beat, in order; `downbeats`
  are indices into `times`; `manual` lists beats moved by hand, which re-analysis keeps.
- A comp's `beats: { "from_layer": "music" }` maps them into comp time through that layer:
  beat `k` is at `layer.in + (times[k] - layer.source_in)` (music layers play at 100 %).
  `b:0` is the first beat at or after the layer's in point.
- The tempo shown in the UI is derived from the mean beat interval; it is never an input.

## Compositions and layers

The After Effects model: a comp has a size, a rate, an `end` (a position; every comp
starts at `0s`), an optional beat map (from a music layer) and an ordered list of
layers. A timeline is a comp; a precomp is a
comp used as a layer.

```json
{
  "id": "main",
  "size": [1080, 1920],
  "rate": "30000/1001",
  "end": "b:128",
  "beats": { "from_layer": "music" },
  "layers": [
    { "id": "music", "type": "audio", "media": "track01", "in": "0s" },
    {
      "id": "shot3",
      "type": "footage",
      "media": "C0001",
      "in": "b:60", "out": "b:68",
      "source_in": "8.2s",
      "time": { "style": "drop-ramp", "at": "b:64", "overrides": { "intensity": 0.7 } },
      "retime": "flow",
      "transform": {
        "scale": { "keys": [ { "t": "b:64", "v": 1.0 },
                             { "t": "b:64+0.5", "v": 1.08, "ease": "out" } ] },
        "position": { "track": "shot3-lock" }
      },
      "grade": "grades/shot3.json",
      "motion_blur": { "shutter_deg": 180 }
    },
    { "id": "title", "type": "graphics", "code": "graphics/lower-third.js",
      "space": "display", "in": "b:64", "out": "b:72",
      "params": { "text": "Event name 2026" } }
  ]
}
```

### Layer types

| type | content |
| --- | --- |
| `footage` | a media record, with source in point, time remap, `retime` (`nearest`, `blend`, `flow`), transform, grade |
| `still` | an image; same transform and grade as footage |
| `audio` | a media record or a generated sound; gain, fades, effects ([07-audio.md](07-audio.md)) |
| `solid` | a colour |
| `shape` | vector paths, fills, strokes, trim paths |
| `text` | text with styling and per-character animators |
| `graphics` | code-driven graphics ([05-motion-graphics.md](05-motion-graphics.md)) |
| `comp` | a precomp |
| `adjustment` | applies its effects and grade to everything below it |

Every visual layer has: `in`/`out`, `transform` (anchor, position, scale, rotation,
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

The canonical curve is **source time against output time**, After Effects' time remap,
stored as keyframes in `time.remap`. Speed is its derivative, and the speed graph is a
view of it, not a second store. A footage layer's `time` is one of:

- absent: plays at 100 %;
- `{ "speed": 2.0 }`: constant speed, shorthand for a straight remap line;
- `{ "remap": [...] }`: source-time keyframes;
- `{ "style": "...", "at": "...", "overrides": {...} }`: a style instance.

A style or a speed graph edit describes speed; it is compiled into remap keys when it is
placed. Between two keys the source position is

```
source(t) = source(t_k) + ∫ speed(u) du   from t_k to t
```

integrated exactly for constant and linear speed segments, and to a stated tolerance
(a hundredth of a source frame) for eased segments, whose integral is not itself a
bezier. Evaluating a frame then needs only the remap curve, so a random seek costs the
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
  Linking gives what Resolve gets from shared nodes and colour groups.
- A style can only use effects the engine already has. When a style needs a new kind of
  effect, that effect is added to the engine as code with a test, and the style refers to
  it by name. Styles stay data the UI can edit.

## Validation

`lc validate` checks a project against the schema and then against meaning: every
reference resolves, every clip has a decided colour space, every placement has enough
source, every beat literal falls inside the beat map, every expression parses. The UI
runs the same check on load and after every external change.
