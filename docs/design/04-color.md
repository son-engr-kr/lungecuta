# Colour

Sources for every constant below are in `docs/research/color-sources.md` (D5); numbers
measured here are in `bench/RESULTS.md`. Nothing is reconstructed from memory.

## The pipeline

```
media colour space --input transform--> DaVinci Wide Gamut
                                          |  clip node graphs (OCIO grading transforms, keys, windows)
                                          |  composite in stack order, DaVinci Intermediate by default
                                          |  adjustment layers on everything below them
                                          v
                               --output transform--> display (Rec.1886 Rec.709) via a view
                                          |
                               top overlays (display-referred), then encode
```

- **Reference implementation: OpenColorIO 2.6, built-in
  `studio-config-v5.0.0_aces-v2.1_ocio-v2.6`**, BSD-3-Clause. It contains DaVinci Wide
  Gamut and DaVinci Intermediate, S-Log3 with S-Gamut3 and S-Gamut3.Cine, D-Log D-Gamut,
  Apple Log and Apple Log 2, BMDFilm Gen5, ARRI, Canon, Panasonic and RED, and Rec.1886,
  sRGB, Display P3, HLG and PQ displays.
- **Working space: DaVinci Wide Gamut**, because it is what the user grades in today, its
  primaries and DaVinci Intermediate curve are published by Blackmagic, and it is in the
  config. DI puts 18 % grey at 0.336043 and linear 100.0 at 1.0.
- **Compositing happens in DaVinci Intermediate by default**, which is what Resolve does
  in a DWG/DI timeline, so blends look the way the user expects; a comp can switch to
  linear-light compositing. Motion blur and frame interpolation always average in linear
  light, whatever the comp's setting.
- **Year one runs OCIO's CPU processors on threads**, for preview and final
  ([01-architecture.md](01-architecture.md#compute-plan)).

## Input: camera spaces

| source | transform | source of the definition | caveats |
| --- | --- | --- | --- |
| Sony S-Log3 / S-Gamut3.Cine | OCIO `S-Log3 S-Gamut3.Cine` | Sony technical summary V1.01; Sony-provided ACES transform | Sony: "S-Log3 is recorded as Full range in XAVC"; the user's clip is flagged `pc`, consistent. To be verified on a chart ([02-media.md](02-media.md#decode)) |
| Sony S-Log3 / S-Gamut3 | OCIO `S-Log3 S-Gamut3` | same | |
| Sony S-Log2 / S-Gamut | custom colour space: Sony's S-Log2 curve plus Sony's ACES matrices | Sony S-Log2 technical paper V1.0; Sony-provided ACES CTL | S-Gamut has **two** matrices, daylight and tungsten, chosen by the camera's colour temperature setting, which has to be known per clip. The curve reads absolute code values like S-Log3; the file's range governs YCbCr decode only |
| DJI D-Log / D-Gamut | OCIO `D-Log D-Gamut` | DJI white paper rev 1.0 (2017), X9 edition (2022) | OCIO's toe is not exactly the paper's `6.025 in + 0.0929` (its linear slope defaults to the log slope at the break); the difference is measured, and the paper's form used if it matters. A body's "D-Log" is mapped here only after a grey-scale shot confirms the paper's code values (10-bit 95, 408 and 586 for 0, 18 and 90 % reflectance): the Osmo Pocket 4P offers a D-Log mode alongside D-Log 2 (a LUT vendor reports its D-Log LUT is identical to the Mavic 4 Pro's, unverified), and if that is the published encoding it is the exact DJI path today |
| DJI D-Log M | DJI's official `.cube` (to Rec.709 only), then the **inverse output view** into the working space | DJI LUT downloads (33^3, output 0 to 1, one file shared by twelve products) | curve and gamut are not published, so the clip has a Rec.709 clip's latitude, and the UI says so. Two things to settle on a real D-Log M file: the LUT's input normalization (it is a Resolve cube, applied to Resolve's data-level-scaled values), and how much of it is a look (some product pages call the same file "vivid"). Not through `Camera Rec.709`, which would tone-map DJI's rendering a second time and leave it dull |
| DJI D-Log 2 (Osmo Pocket 4P, 10-bit, 1x zoom only) | as D-Log M: DJI's official LUT, then the inverse output view | none published, checked 2026-10-11: no curve or gamut document from DJI, and no Osmo Pocket 4P entry on dji.com/lut. A third-party LUT vendor says it does not use D-Gamut and that Resolve has no CST for it (unverified) | until DJI publishes a definition or a LUT is in hand, a D-Log 2 clip stays undecided. Whatever arrives first (white paper or LUT) decides how it enters; a published curve and gamut would give it a real input transform, like S-Log3 |
| Apple Log | OCIO `Apple Log` | Apple Log Profile white paper (2023) | files signal it through CoreVideo attachments, not the usual tags |
| Apple Log 2 | OCIO `Apple Log 2` | Apple documentation; OCIO and Academy sources for the gamut | Apple's own Log 2 paper not read (needs a login) |
| HLG camera footage | **none yet; such clips import as undecided** | BT.2100 | the user's Sony HLG clips are tagged `hlg-slog3ootf` in their XML. OCIO's config offers HLG only as a display. What Sony means by `slog3ootf`, and whether to treat the footage as display-referred HLG or to invert the OOTF into scene light, has to be established from Sony documentation before a mapping row is added. Deferred by the user (2026-10-13): the footage being edited is S-Log3 |
| Rec.709 camera footage (phones in SDR) | OCIO `Camera Rec.709` | BT.709 | |
| screen recordings, UI captures | display-referred ([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)) | | their code values must come out as captured |

Every row marked "no file yet" in [02-media.md](02-media.md#colour-space-detection) is
confirmed on a real file before it is relied on.

## Grading

### Node graph

Resolve's model, because it is the one the user knows:

- each clip has a node graph; a timeline grade is an adjustment layer at the top of the
  stack, so it grades exactly what is below it;
- nodes are serial; parallel and layer-mixer nodes come later;
- a node holds an ordered list of operations, an optional key (qualifier, window, or both)
  and a mix amount;
- a look is a style made of nodes, linked across clips, which is what Resolve's shared
  nodes and colour groups provide.

Written so far (`src/lungecuta/grade.py`): a footage layer's `grade` is a list of
operations (one node over the whole frame) or `{ "nodes": [...] }`, serial nodes each with
`ops`, an optional `window` (rectangles and ellipses as a mask's, in the clip's frame as
shown, feathered, inverted, combined) and a `mix`; a node gives
`input + mix * window * (graded - input)`, in the working space, before the layer is
placed. A node's `key` is a qualifier on its input: `hue` (centre and width), `sat` and
`luma` ranges (low and high), each with a `soft` margin over which the key falls linearly
to 0 (D15: the shape of the softness is not taken from Resolve), multiplied, and
optionally inverted. Hue, saturation and luma are OCIO's LOG-style HSY, the conversion
`GradingHueCurveTransform` uses (OCIO 2.6.0, `FixedFunctionOpCPU.cpp`, run through OCIO's
own `FIXED_FUNCTION_RGB_TO_HSY_LOG`): hue in [0, 1) with magenta at 0, red at 1/6, green
at 1/2 and blue at 5/6; saturation 4 times the sum of each channel's distance from luma;
luma with Rec.709 weights, all on the working-space codes. A grey has hue 0 there, so a
hue range near magenta wants a saturation range beside it. A node gives
`input + mix * key * window * (graded - input)`. A node may name a look instead of holding
operations, `{ "look": "warm-film", "mix": 0.7 }`: a style in `styles/` with a `grade`
(operations, or serial nodes of them, none naming another look), whose whole grade is the
node's, keyed and mixed like any node's. It is linked, read from the style on every frame,
so editing the look changes every clip that uses it, Resolve's shared nodes; the mix is
its intensity. `lc op look COMP LAYER LOOK --mix M` puts one after the layer's own grade
(the same look again takes the new mix rather than a second node), `lc op unlook` takes it
off, and the inspector does both. A render's manifest lists the looks it read, so
`lc status` and the editor's preview go stale when one changes. Windows that follow a
track, parallel and layer-mixer nodes and key softening (denoise, shrink and grow) are
not written yet.

### Operations come from OpenColorIO

OCIO 2.6 already ships the grading operators, with CPU and GPU paths and dynamic
properties for live edits from the UI. lungecuta uses them rather than writing its own:

| operation | OCIO transform | style |
| --- | --- | --- |
| lift, gamma, gain, offset, exposure, contrast with pivot, saturation, black and white clip | `GradingPrimaryTransform` | LOG on DI-encoded data, LIN on linear |
| log wheels: blacks, shadows, midtones, highlights, whites | `GradingToneTransform` | LOG |
| custom curves: master, R, G, B | `GradingRGBCurveTransform` | LOG |
| hue vs hue, hue vs sat, hue vs lum, lum vs sat, sat vs sat, lum vs lum, sat vs lum | `GradingHueCurveTransform` | its own hue model |
| ASC CDL | `CDLTransform` | per the CDL specification (Rec.709 weights for saturation, by definition) |
| LUT | `FileTransform` | tetrahedral interpolation; the LUT reference must name its input and output spaces, because a `.cube` declares neither |
| colour space transform | any OCIO processor | |

OCIO's LOG-style defaults (contrast pivot, tone zone ranges) are designed around a log
encoding such as ACEScct, not DaVinci Intermediate. For the primary grade this is now
settled from OCIO 2.6.0's source (`GradingPrimary.cpp`, `GradingPrimaryOpCPU.cpp`) and
measured on OCIO itself (`tests/test_grade.py`):

- the contrast turns around the code value 0.5 + 0.5 pivot; OCIO's default pivot, -0.2,
  puts it at 0.4, near ACEScct's 18 % grey (0.4135) and well above DI's (0.336043);
- one unit of LOG brightness adds 6.25/1023 to every code value;
- LOG gamma raises code values between the black and white pivots (0 and 1 by default) to
  1/gamma, so a gamma above 1 lifts the middle;
- saturation mixes towards a luma with Rec.709 weights (0.2126, 0.7152, 0.0722) whatever
  the encoding, so on DWG/DI values it is not a constant-luminance saturation.

lungecuta's `primary` operation therefore states its controls in the working space's
code values: `offset` is added as it is (converted to OCIO's brightness units), and
`pivot` is the code value contrast turns around, by default the working space's encoding
of 18 % grey, computed through the config (0.336043 for DI). The tone zones of
`GradingToneTransform` are not mapped yet and that operation is not offered.

What OCIO does not provide is written here, each with a numpy reference:

| operation | notes |
| --- | --- |
| white balance, temperature, tint | chromatic adaptation in linear DWG (CAT02, as the ACES camera transforms use) |
| qualifier (HSL key) | the same hue and saturation definition as `GradingHueCurveTransform`, so a curve and a key agree on what "orange" is; softness, key denoise, shrink and grow |
| windows: circle, linear, polygon, bezier | feather, inside/outside, follow a track ([06-editing.md](06-editing.md#tracking)) |
| blur, sharpen | linear light, per node, keyable |
| film look | grain seeded per frame, halation in linear light, near the end of the graph |
| shot match | Pickapicka's fit (mean plus covariance transport, then smoothed histogram match), fitted on DI frames |

### What comes from Pickapicka

The survey (D3) found Pickapicka display-referred and 8-bit with no colour management.
What carries over is structure and two operators: the edit schema pattern (normalize,
sparse sub-objects, `is_neutral`, process versions with `upgrade`, content hash), presets
as parts with Amount, mask geometry and combination operators with alpha caches, the
window-and-padding render contract, the colour-match fit, and the sharpening operator.
Its `cv2.remap` LUT path is not used: OpenCV's remap carries weights in 1/32 steps, and
OCIO's `FileTransform` interpolates tetrahedrally.

## Output

### Views

The output transform is an OCIO display and view. Offered for the Rec.1886 Rec.709
display:

| view | what it is | licence |
| --- | --- | --- |
| `ACES 2.0 - SDR 100 nits (Rec.709)` | published tone and gamut mapping; maps DI 0.336 (18 % grey) to 0.3831 | Apache-2.0 |
| `Video (colorimetric)` | no tone mapping, highlights clip | n/a |
| a LUT exported from the user's own Resolve | Resolve's Color Space Transform with DaVinci tone mapping, generated from a CST node as a 65^3 cube (the free version can export it) | the user's own file, never shipped with lungecuta; accurate only to what a 65^3 cube can hold ([bench/RESULTS.md](../../bench/RESULTS.md)) |

Resolve's "DaVinci" tone and gamut mapping is not published, so the third row is the only
way to match it. OpenDRT is GPL-3.0 and AgX carries no licence, so neither ships by
default.

### Viewing conditions and the Mac

A grade is made for a viewing condition, and that condition is chosen explicitly per
project: a BT.1886 (gamma 2.4) reference display by default. What macOS does with tags
is measured (`bench/cvtest/`, macOS 26.4.1), as decode APIs rather than pixels on a screen:

- 1-1-1 and 1-2-1 are both decoded with a pure gamma of **1.9609375**, so a 1-2-1 tag does
  not avoid the lighter look in QuickTime and Safari;
- 1-13-1 is decoded with the sRGB curve, and 1-4-1 at about gamma 1.79: tags do change the
  Mac decode;
- an untagged clip's primaries depend on its frame size: SMPTE-C at 256x64, BT.709 at
  720x480 and 1920x1080.

The viewer offers two monitoring modes: **reference** (what the BT.1886 display shows) and
**as a Mac plays the 1-1-1 deliverable** (gamma 1.961), so the lighter look is seen before
a client sees it. Frames are sent to Chrome sRGB-encoded and Chrome maps them to the
display; the engine does not also convert into the display profile, which would manage
colour twice. What finally reaches the screen in QuickTime, Safari and Chrome is checked
by screen capture, because decode APIs do not say what a compositor does
(Chromium, for one, has a separate `BT709_APPLE` transfer for hardware-overlay video).

## Graphics

Graphics layers declare a `space`:

- **`display`** (the default for titles, UI-style graphics, captures, screen recordings):
  code values are the deliverable's code values. CSS `#808080` comes out as code 0.502 in
  the Rec.709 file. Such a layer is a top overlay or enters through the inverse view,
  depending on its place in the stack ([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)).
- **`scene`**: the layer is graded with the footage. Its sRGB-encoded values are decoded
  with the sRGB curve (code 0.5 is 0.214 linear; under BT.1886 it would be 0.189, so the
  curve is stated, not assumed) and converted to linear DWG, or, for Skia-rendered layers
  (tier 2), taken as float linear directly. Bright elements can then exceed diffuse
  white and go through the tone mapping like footage.

## Precision and tests

- float32 throughout the footage path; proxies and outputs at 10-bit or better; no 8-bit
  table or round trip in the footage path. Web graphics are 8-bit sRGB by nature (D8)
  and enter as display-referred material.
- **Every faster path is tested against OCIO's CPU processor on a dense, saturated
  pattern** (the full DI cube sampled, not a grey ramp): at most 1 ten-bit output code
  anywhere on the pattern (*proposed*). A grey ramp alone hides the failures that matter:
  a 129^3 LUT is within 0.34 codes on grey and still off by up to 64.5 on near-neutral
  colours (`bench/ocio_lut_error.py`).
- Against Resolve, on a chart that includes saturated patches: median within 1 code, p99
  within 2.
- Every render is decoded again by `lc verify`, which measures the mean signed error per
  channel on patches against the engine's reference: a matrix or range error is a bias
  and shows up there, where PSNR would blur it with codec loss.

## HDR

Not in the first versions. OCIO offers `Rec.2100-HLG` and `Rec.2100-PQ` displays with
ACES 2.0 views. YouTube and Vimeo accept PQ and HLG; PQ files should carry ST 2086
mastering metadata and MaxCLL/MaxFALL, and YouTube accepts an optional `.cube` as an SDR
down-conversion hint.
