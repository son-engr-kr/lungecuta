# Pickapicka's render engine: what transfers to scene-referred video

Survey of `src/pickapicka` in [Pickapicka](https://github.com/son-engr-kr/pickapicka), the
user's photo editor, made 2026-10-10 to decide what lungecuta can reuse. Line references
are to that tree at that date and will drift.

## Verdict

The engine is display-referred, 8-bit in and 8-bit out, with no colour management. It
works on the source file's own gamma-encoded RGB scaled to [0, 1]; RAWs are decoded
straight to 8-bit sRGB. The code says so itself:

> "The colour profile is whatever the original carried, since the pipeline never
> converts colour." (`metadata.py:27-31`)

> "needs colour management, which the pipeline does not have: it never converts
> colour, it only labels it." (`docs/roadmap-parity.md:162-164`)

The structure carries over well. Most of the colour maths does not.

## Pipeline, in order (`editing.render`, `editing.py:2346-2482`)

0. Decode to uint8 (PIL without ICC conversion; rawpy to 8-bit sRGB). Preview base capped at 2048 px.
1. Normalize; a neutral edit returns the input unchanged.
2. Optics: vignetting, distortion and CA in one remap, perspective (uint8 in and out).
3. Pre-grade, cached together: repairs (red-eye, healing), portrait skin, look (`.cube` or colour match, applied under every slider). Each returns uint8.
4. Grade in float32: white balance and tone folded into one per-channel table; dehaze; denoise (luma through 8 bits); texture; clarity; colour mixer; grading wheels; vibrance and saturation; lens blur, motion blur, glow; sharpen; vignette; film (tone through an 8-bit table, halation, grain); pixelate.
5. Clip to [0, 1].
6. Masks: each grades its own bounding box with its own sliders and blends through its alpha.
7. Face reshape (Lanczos).
8. To uint8, then straighten and crop, then watermark.

Mid-pipeline 8-bit round trips: after optics, repairs, portrait and the look; mask crops
when a tone table is active; denoise luma; the film tone table; the brush raster.

## Assumptions that break on DI-encoded DWG

- Diffuse white at 1.0: dehaze's atmospheric light, glow and halation knees, screen
  blends, highlight masks, clarity's midtone mask `1 - (2y - 1)^2`. In DI, 18 % grey is
  0.336 and 1.0 is a hundred times diffuse white.
- Rec.709 luma weights applied to encoded values (`editing.py:1070`, `grading.py:68`,
  `film.py:83`, `sharpening.py:173`).
- OpenCV HLS, HSV, Lab (sRGB curve inside) and YCrCb (BT.601) conversions.
- White balance as gains on encoded values; exposure through the sRGB curve.
- 256- and 1024-sample tables; the film table built with a truncating 8-bit cast.
- Grain seeded per photo (`film.py:371-374`), which would freeze on video.
- Per-photo analyses (segmentation, range selector, auto tone) with no temporal
  smoothing, which would flicker.

## Reusable as-is or nearly

| piece | where | note |
| --- | --- | --- |
| edit schema pattern | `editing.py:59-164`, `735-943` | normalize, sparse sub-objects, `is_neutral`/`is_empty`, process versions with `upgrade`, content hash |
| presets | `presets.py` | parts, absolute values, Amount, tagged layers replaced on re-apply |
| mask geometry and combination | `editing.py:398-1919` | radial, linear, brush, add/subtract/intersect, alpha caches, normalized coordinates; video adds keyframes and tracking |
| window and padding render contract | `editing.py:2279-2579` | maps onto proxy and full-resolution parity |
| LUT engine | `lut.py` | strict `.cube` parser, domain support, `cv2.remap` trilinear, uint16 storage, content keys; raise `_REMAP_CELLS` for 10-bit (0.17/255 is about 0.7 of a 10-bit code) |
| colour-match fit | `lut.py:523-723` | mean plus covariance transport, then smoothed histogram match; fit on DI frames |
| sharpening operator | `sharpening.py` | float, unclipped; swap luma weights, retune the 0.08 edge threshold |

## Reusable with real changes

| piece | change |
| --- | --- |
| grading wheels (`grading.py`) | keep partition-of-unity zones and the luma-indexed gain/offset table; retune the zone splits for DI; tints from DWG luminance |
| tone and white balance (`editing.py:986-1065`) | keep table folding with a float table; exposure in linear DWG, white balance as chromatic adaptation |
| colour mixer (`editing.py:1179-1249`) | keep band interpolation and the low-chroma guard; hue from a perceptual space derived from DWG |
| range masks (`rangemask.py`) | proper Lab (or the rendered proxy), float samples |
| film (`film.py`) | already defined in stops around 0.18 grey; float curve, DI or linear input, per-frame seed; place near the output transform |
| creative effects (`editing.py:1390-1553`) | lens blur, motion blur, glow, vignette in linear DWG |
| segmentation (`segment.py`) | on the display-rendered proxy, with temporal smoothing |

## Photo-only

`raw.py`, `hdr.py`, `exporting.py` and `metadata.py` (8-bit JPEG/TIFF with ICC
pass-through), spatial 8-bit denoise, dehaze, the retouch modules (healing, red-eye,
portrait, reshape, AI fill), auto tone and the white-balance picker as written,
`watermark.py`.

## Timings worth knowing

LUT via `cv2.remap`: 286 ms for 24 MP against 7.0 s for eight gathers. Tone folding:
about 340 ms to 1 ms at 2048 px. Film tone table: about 40 times faster. Banded export
with 4 threads: 3x for a basic grade. No GPU anywhere; every ONNX session is CPU.
