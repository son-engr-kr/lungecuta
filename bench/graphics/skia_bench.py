"""The page.html scene rendered natively with skia-python (CPU raster) into numpy, one process.

Variants: an 8-bit sRGB surface, and an F16 surface in linear Rec.2020. Paint colours are
given in sRGB and Skia converts them to the surface's colour space, so in the F16 variant
blending happens in linear light (the picture differs from the 8-bit one at soft edges).
Text uses Apple SD Gothic Neo (macOS system font) so Hangul shapes the same way as the page.

Usage: uv run --with skia-python==144.0.post2,numpy python bench/graphics/skia_bench.py
"""
import colorsys
import math
import time

import numpy as np
import skia

WARMUP = 3


def make_font(size):
    tf = skia.FontMgr().matchFamilyStyle("Apple SD Gothic Neo", skia.FontStyle.Normal())
    assert tf is not None, "Apple SD Gothic Neo not found"
    return skia.Font(tf, size)


def draw_scene(canvas, W, H, n, font):
    t = n / 60.0
    canvas.clear(skia.Color4f(0, 0, 0, 0))
    s = W / 1080.0
    grad = skia.GradientShader.MakeLinear(
        points=[(0, 0), (W, H)],
        colors=[skia.Color4f(20 / 255, 40 / 255, 120 / 255, 0.6), skia.Color4f(200 / 255, 60 / 255, 90 / 255, 0.6)],
    )
    canvas.drawRect(skia.Rect.MakeWH(W, H * 0.25), skia.Paint(Shader=grad))
    paint = skia.Paint(AntiAlias=True)
    for i in range(150):
        x = (math.sin(i * 12.9898 + t * 0.7) * 0.5 + 0.5) * W
        y = (math.cos(i * 78.233 + t * 0.5) * 0.5 + 0.5) * H
        canvas.save()
        canvas.translate(x, y)
        canvas.rotate(math.degrees(t + i))
        a = 0.5 + 0.5 * math.sin(i + t)
        r, g, b = colorsys.hls_to_rgb(((i * 17 + n) % 360) / 360.0, 0.55, 0.7)
        paint.setColor4f(skia.Color4f(r, g, b, a))
        canvas.drawRRect(skia.RRect.MakeRectXY(skia.Rect.MakeXYWH(-40 * s, -20 * s, 80 * s, 40 * s), 10 * s, 10 * s), paint)
        canvas.restore()
    tp = skia.Paint(AntiAlias=True, Color4f=skia.Color4f(1, 1, 1, 1))
    for i in range(12):
        canvas.save()
        canvas.translate(W * 0.5, H * (0.3 + i * 0.05))
        canvas.rotate(math.degrees(math.sin(t + i) * 0.1))
        text = f"Frame {n} 안녕하세요 모션 그래픽 {i}"
        blob = skia.TextBlob.MakeFromShapedText(text, font)
        w = font.measureText(text)
        canvas.drawTextBlob(blob, -w / 2, 0, tp)
        canvas.restore()


def surface_info(W, H, variant):
    if variant == "u8-srgb":
        info = skia.ImageInfo.Make(W, H, skia.ColorType.kRGBA_8888_ColorType, skia.AlphaType.kPremul_AlphaType,
                                   skia.ColorSpace.MakeSRGB())
        return info, np.uint8
    assert variant == "f16-linear-rec2020", variant
    cs = skia.ColorSpace.MakeRGB(skia.cms.NamedTransferFn.kLinear, skia.cms.NamedGamut.kRec2020)
    info = skia.ImageInfo.Make(W, H, skia.ColorType.kRGBA_F16_ColorType, skia.AlphaType.kPremul_AlphaType, cs)
    return info, np.float16


def bench(W, H, nframes, variant):
    info, dtype = surface_info(W, H, variant)
    surface = skia.Surface.MakeRaster(info)
    assert surface is not None
    font = make_font(54 * W / 1080.0)
    out = np.empty((H, W, 4), dtype=dtype)
    for n in range(WARMUP):
        draw_scene(surface.getCanvas(), W, H, n, font)
        assert surface.readPixels(info, out, out.strides[0], 0, 0)
    t_draw = 0.0
    t0 = time.perf_counter()
    for n in range(nframes):
        a = time.perf_counter()
        draw_scene(surface.getCanvas(), W, H, n, font)
        t_draw += time.perf_counter() - a
        assert surface.readPixels(info, out, out.strides[0], 0, 0)
    dt = time.perf_counter() - t0
    print(f"skia-python {variant} {W}x{H}: {nframes / dt:.1f} fps, {1000 * dt / nframes:.1f} ms/frame "
          f"(draw {1000 * t_draw / nframes:.1f} ms)", flush=True)


if __name__ == "__main__":
    print("skia-python", skia.__version__)
    for (W, H, nf) in [(1080, 1920, 60), (3840, 2160, 30)]:
        for v in ("u8-srgb", "f16-linear-rec2020"):
            bench(W, H, nf, v)
