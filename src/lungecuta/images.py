"""Images for looking at: 8-bit PNGs with no colour chunks, and the text on them.

Text is drawn with Pillow, since ffmpeg's drawtext escaping broke on labels holding `:`
and spaces in a past project's review tools. The font is the Aileron Regular subset that
Pillow bundles (CC0, dotcolon.net), so no system font is needed."""
from __future__ import annotations

import functools
import struct
import zlib

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_SIZE = 16


def quantize(frame: np.ndarray) -> np.ndarray:
    """Display-encoded float values to 8-bit codes, clipped to [0, 1] and rounded half up."""
    return np.floor(np.clip(frame, 0, 1) * np.float32(255) + np.float32(0.5)).astype(np.uint8)


def png8(image: np.ndarray) -> bytes:
    """An 8-bit RGB PNG with no colour chunks (no gAMA, cHRM, sRGB, iCCP or cICP), so its
    codes are the image's codes."""
    assert image.dtype == np.uint8 and image.ndim == 3 and image.shape[2] == 3, "expected uint8 HxWx3"
    height, width, _ = image.shape
    rows = np.zeros((height, 1 + width * 3), np.uint8)  # each row starts with filter type 0 (none)
    rows[:, 1:] = image.reshape(height, width * 3)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit truecolour, no interlace
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows.tobytes())) + chunk(b"IEND", b"")


def png(frame: np.ndarray) -> bytes:
    """The PNG of a display-encoded float frame."""
    return png8(quantize(frame))


@functools.cache
def font() -> ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=FONT_SIZE)


def text_height() -> int:
    """Lines one row of label text needs, descenders included."""
    left, top, right, bottom = font().getbbox("Agf:0123456789.s")
    return bottom


def draw_text(image: np.ndarray, items: list[tuple[tuple[int, int], str, tuple[int, int, int]]]) -> None:
    """Draw each (top-left corner, text, colour) onto a uint8 HxWx3 image, in place."""
    canvas = Image.fromarray(image)
    draw = ImageDraw.Draw(canvas)
    for position, text, fill in items:
        draw.text(position, text, fill=fill, font=font())
    image[...] = np.asarray(canvas)
