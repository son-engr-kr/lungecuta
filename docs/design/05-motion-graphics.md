# Motion graphics

## What it has to reproduce

Every element of four past projects (full catalogue: D4 in
[decisions.md](decisions.md)). Grouped by the web technology each one needed:

| needs | elements |
| --- | --- |
| DOM and CSS | kinetic type (per-character rise through `overflow:hidden`, stagger, exit), "slam" (scale, blur, RGB `text-shadow` split), typewriter over styled runs with a block cursor, terminal and editor chrome, word-by-word subtitles with `box-decoration-break`, chips and HUDs, keycaps, `clip-path` and `mask-image` reveals, `background-clip: text` light sweeps, `backdrop-filter` panels, CSS-3D card flies, stacks, flips and explosions |
| Canvas2D | photo flash, `getImageData` RGB split, slice glitch, grain tiles, scanlines, vignette, gradient scrims, oscilloscope traces with glow, progress dials, wipes, brand bars, rings, photo walls from atlases, thumbnail tunnels, shadowed rounded cards |
| WebGL (Three.js 0.186) | data-driven 3D (sim bones and muscle tubes rebuilt per frame), custom shader materials (Fresnel rim, activation ramps, reveal along length, scan planes), instanced crowds read from float textures, an fwidth grid with contact ripples, and a post chain: HalfFloat target with MSAA, bloom, a linear-light FX pass (band glitch, radial chromatic aberration, flash), ACES tone mapping, display-space vignette, grain and fade |
| fonts | Pretendard (Korean), Inter Tight, JetBrains Mono, Space Grotesk; Greek and Korean glyphs loaded before the first frame |
| data | sim exports as typed arrays, `stats.json` for on-screen claims, Whisper word timings, capture event logs |

That list settles one question before any library is chosen: the graphics layer needs
**the whole web platform**, DOM, CSS, Canvas2D and WebGL together in one page. A
canvas-only or vector-only engine (Lottie, Skia, a Canvas2D scene graph) cannot render
half of it.

## The contract every past page already met

Each page exposed `window.renderAt(t)`: a pure function of time that draws frame `t`
completely, with no CSS transitions, no `Date.now()`, seeded randomness, and a reset
of all state at the start of each frame. That contract is kept, generalised:

```js
export default {
  params: { text: { type: "string", default: "" }, accent: { type: "color" } },
  setup(ctx) { /* build DOM, load fonts and data once; ctx gives size and params */ },
  render(ctx, t) { /* draw frame t completely; t in seconds of layer time */ },
  events() { return [ { t: 0.42, name: "key" } ]; },  // for SFX placement (07-audio.md)
};
```

- `params` are exposed to the comp: a param can be a constant, keyframed in the
  graph editor, or driven by an expression, exactly like any other layer property. That
  is how a code-driven graphic stays editable by hand: the code decides what the graphic
  is, its parameters decide how it moves, and the parameters live in the project file.
- `t` is layer time, measured from the layer's `start`, not from its in point, so trimming
  the head of a title does not restart its animation, as in After Effects
  ([03-project-format.md](03-project-format.md#layer-timing)). `events()` times are layer
  time too.
- `ctx` also carries the comp's beat map, so a graphic can be written in beats.
- `events()` replaces the old `cue()` side file, so sound and picture share one
  definition of when a key was pressed.

## Engine choice

Decision D8 in [decisions.md](decisions.md). In short: two tiers behind one layer
interface, web first.

### What was measured

On this Mac (M5 Pro), one light synthetic scene (gradient, 150 rotated alpha rounded
rects, 12 lines of rotated Latin and Hangul text), frame seeked then delivered to Python
as an array (`bench/graphics/`):

| path | 1080x1920 | 3840x2160 |
| --- | --- | --- |
| those projects' previous pipeline (30 s at 60 fps in 8.5 min) | 3.5 fps | n/a |
| CDP `captureScreenshot` PNG, 4 pages in parallel | 48.1 fps | 19.1 fps |
| Canvas2D `getImageData` over a chunked WebSocket, RGBA8 | 38.4 fps | 10.9 fps |
| Canvas2D float16 | 22.0 fps | 5.4 fps |
| WebGL2 RGBA16F `readPixels` | 10.9 fps | 2.8 fps |
| skia-python CPU raster, RGBA8, 1 process | 279 to 313 fps | 40 to 43 fps |
| skia-python F16 linear Rec.2020, 1 process | 234 to 270 fps | 42 to 44 fps |

Facts that shaped the choice:

- **On macOS, Chromium refuses `BeginFrameControl`** ("not supported on MacOS yet" in
  `headless/lib/browser/protocol/target_handler.cc`), so deterministic capture on a Mac
  is seek-then-screenshot.
- In Chrome the bottleneck is getting pixels out, not drawing them.
- Linear canvas colour spaces (`srgb-linear`) are rejected by Chrome 153 and 155; float16
  canvases are Chrome-only and experimental. Web graphics are 8-bit sRGB in practice.
- Remotion's licence charges organisations of four or more and forbids running users'
  own code as a service; GSAP's licence prohibits visual no-code animation tools, so it is
  never bundled; Theatre.js's studio is AGPL-3.0 and dormant; Motion Canvas seeks by
  replaying from the start of a scene, which fails random access; lottie-web is in
  maintenance mode.

### Tier 1: web layers, in one pinned Chromium

Every past page becomes a web layer with little change, because the contract above is
the `renderAt(t)` they already met. Built-in layer types (text with per-character
animators, shapes, images, solids) are a small lungecuta JS runtime that uses the same
contract, so built-in and custom layers go through one renderer.

- **Editing is live.** In the editor the layer's page runs at the playhead over the
  engine's frame, as an interaction preview. Web layers are display-referred; at the top
  of the stack they are composited after the output transform on display-encoded values,
  which is also how the browser overlays them, so the live view is close to the render.
  How close is measured: a captured title is diffed against its live overlay. The
  engine's render is the truth ([01-architecture.md](01-architecture.md#where-display-referred-layers-enter)).
- **That only holds if the editor and the capture use the same Chromium build.** The UI
  therefore runs in the pinned build that renders, not in Safari's WebKit and not in
  whatever Chrome the user happens to have. Playwright 1.63.0 bundles both halves at one
  version: chrome-headless-shell 153.0.8010.12 for capture and Chrome for Testing
  153.0.8010.12 for the editor, launched directly with `--app=http://127.0.0.1:PORT` and
  its own profile directory. Measured: the same page with DOM and canvas text, Latin and
  Hangul at 12 to 120 px, letter-spacing, rotation and a variable-weight sweep rendered
  pixel-identically in headless shell 153, Chrome for Testing 153 and Chrome 155, at scale
  1 and 2 (headless, sRGB profile forced). Drift from auto-updating system Chrome is the
  real risk the pin removes.
- **Final render** captures from a pool of headless pages, one browser per worker:
  48 fps at 1080x1920 in the measurement, so a 30 s vertical piece at 60 fps renders its
  graphics in about 40 s, against 8.5 minutes before.
- **Precision is 8-bit sRGB with straight alpha** (measured: a fill of
  `rgba(255,128,0,0.25)` captures as `[255,128,0,64]`). Composited as an overlay the error
  stays within about one code value. Two limits follow from the canvas storing
  premultiplied 8-bit internally:
  - faint pixels carry wrong colours (a constant orange glow ends in pixels like
    `[255,255,0,1]`), which becomes visible only when the result is pushed: over black at
    +4 stops the glow's tail has 23 distinct levels with steps of 71 codes, against 56
    levels and steps of 12 from a float16 canvas. So web layers stay display-referred
    overlays and are not graded;
  - additive light cannot be represented at all (a premultiplied canvas cleared to
    `(0.5, 0.3, 0, 0)` captures as transparent black). Flashes, glows and light leaks are
    therefore either engine effects, or web layers rendered opaque on black and
    composited with an `add` or `screen` blend mode the layer declares.

### Tier 2: native layers in the engine (when measured need appears)

The same layer model rasterised by Skia (skia-python, BSD-3) inside the engine, in
float16 and in either a linear or a display-encoded space:

- scene-referred graphics that should be graded and tone-mapped with the footage;
- graphics-heavy 4K work where 19 fps of capture becomes the bottleneck;
- Lottie import through Skottie or ThorVG.

A text or shape layer can move between tiers because the project file describes it the
same way. skia-python has one active maintainer, so the wheels are pinned and the
rasteriser stays behind a thin interface.

## Phase 0: graphics for Resolve

`lc graphic` renders a page to ProRes 4444 with alpha for Resolve, the past projects'
approach (seek `window.renderAt(t)`, capture, encode) in the pinned Chromium through
Playwright: the capture keeps a transparent background, which the screenshot returns with
straight alpha (`bench/graphics/`); the 8-bit sRGB codes become BT.709 Y'CbCr in limited
range, 10-bit 4:4:4 with alpha, tagged 1-1-1 on the frames themselves (ffmpeg's encoder
tags alone left primaries and transfer unset); the page is served only its own directory
and any other request fails the render, as does a page error. Read back by lc's H.273
decode, every patch of the patch card returns its 8-bit code within 0.35
(`tests/test_graphics.py`). A comp's `graphics` layers use the same capture: `lc render`
draws each over the frame after the output view, on display-encoded values with straight
alpha, as the spike measured within a code of the live page; `lc fcpxml` renders each to
ProRes 4444 over its visible frames and places it on a lane above the cuts. The card
answers a Phase 0 question: does Resolve send a
Rec.709 graphic in a DWG/DI timeline through the output tone mapping
([10-roadmap.md](10-roadmap.md#phase-0-the-baseline-days)).

## Capture lessons that carry over regardless

From the past projects' render scripts:

- after `render`, wait for `gl.finish()` and two `requestAnimationFrame`s before
  capturing, or the previous frame is captured;
- one browser per worker, because a second tab is a background tab and its rAF and image
  decoding stall;
- random ports for the local server;
- fonts awaited per weight and per script before the first frame;
- 0.26 to 0.33 s per 1080p frame by screenshot in those projects; the pooled capture
  measured above is about fourteen times faster, and preview captures run at proxy size.
