"""Web graphics to ProRes 4444 with alpha, for import into Resolve (Phase 0 task 7).

A page keeps the contract the past projects' pages met (05-motion-graphics.md): it sets
`window.ready = true` once it has loaded what it needs, and `window.renderAt(t)` draws the
frame at `t` seconds completely. As the past renderer did, each frame is seeked and then
captured, since Chromium has no deterministic frame control on macOS; unlike it, the
capture keeps a transparent background (straight alpha, `bench/graphics/`), and the page is
served only its own directory, with every other request refused (the graphics sandbox,
09-agent-interface.md#sandboxing).

The frames are 8-bit sRGB code values (D8). They go into the file as Y'CbCr by the BT.709
matrix in limited range, 10-bit 4:4:4 with a 16-bit alpha, and the file is tagged
BT.709 (1-1-1), so a decoder that follows the tags gets the page's code values back.
"""
from __future__ import annotations

import functools
import http.server
import io
import json
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from urllib.parse import urlencode

from .ticks import format_rate
from .tools import run, tool

# The flags the capture measurements used (bench/graphics/README.md).
CHROMIUM_ARGS = ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu-rasterization",
                 "--force-color-profile=srgb"]
READY_TIMEOUT_MS = 120_000  # what the past renderer waited for `window.ready`
TO_YCBCR = "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int"
# The frames carry their own colour properties into the encoder, so they are set on the frames.
TAGS = "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def serve(directory: Path) -> Iterator[str]:
    """Serve `directory` on 127.0.0.1 at a free port; yields its origin."""
    handler = functools.partial(_Quiet, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def opened(page_file: Path, size: tuple[int, int], params: dict[str, Any] | None = None) -> Iterator[Any]:
    """A Chromium page of `size` showing `page_file`, ready to render; `params` reach the
    page as JSON in its `params` query parameter."""
    from playwright.sync_api import sync_playwright

    page_file = page_file.resolve()
    assert page_file.is_file(), f"{page_file} is not a file"
    width, height = size
    with serve(page_file.parent) as origin, sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=1)
        refused: list[str] = []

        def gate(route) -> None:
            url = route.request.url
            if url.startswith(origin + "/"):
                route.continue_()
            else:
                refused.append(url)
                route.abort()

        context.route("**/*", gate)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        query = "" if not params else "?" + urlencode({"params": json.dumps(params)})
        page.goto(f"{origin}/{page_file.name}{query}")
        page.wait_for_function("window.ready === true", timeout=READY_TIMEOUT_MS)
        assert not errors, f"{page_file.name} raised: {errors[0]}"
        yield page
        assert not errors, f"{page_file.name} raised: {errors[0]}"
        assert not refused, f"{page_file.name} asked for {refused[0]}, outside its directory; refused"
        browser.close()


def frame_png(page: Any, t: float) -> bytes:
    """Frame `t` (seconds) as a PNG with a transparent background."""
    page.evaluate("t => window.renderAt(t)", t)
    return page.screenshot(type="png", omit_background=True)


def render(page_file: Path, out: Path, *, size: tuple[int, int], rate: Fraction, frames: int,
           first_time: Fraction = Fraction(0), params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Render `frames` frames of a page at `rate`, the first at page time `first_time`
    seconds, into a ProRes 4444 file with alpha."""
    assert frames >= 1, "render at least one frame"
    assert out.suffix == ".mov", f"ProRes 4444 goes into a .mov file, not {out.name}"
    partial = out.with_name(f".{out.stem}.partial.mov")
    command = [
        tool("ffmpeg"), "-v", "error", "-y", "-f", "image2pipe", "-c:v", "png", "-framerate", format_rate(rate),
        "-i", "-", "-vf", f"{TO_YCBCR},format=yuva444p10le,{TAGS}",
        "-c:v", "prores_ks", "-profile:v", "4", "-pix_fmt", "yuva444p10le",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
        str(partial),
    ]
    with opened(page_file, size, params) as page:
        encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        for index in range(frames):
            encoder.stdin.write(frame_png(page, float(first_time + Fraction(index) / rate)))
        encoder.stdin.close()
        error = encoder.stderr.read().decode(errors="replace").strip()
        assert encoder.wait() == 0 and not error, f"ffmpeg failed encoding {out.name}: {error}"
    stream = json.loads(run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
        "stream=nb_read_packets,pix_fmt,width,height,color_space,color_transfer,color_primaries,color_range",
        "-of", "json", str(partial),
    ]).stdout)["streams"][0]
    assert int(stream["nb_read_packets"]) == frames, f"{out.name} has {stream['nb_read_packets']} frames for {frames}"
    assert (stream["width"], stream["height"]) == size, f"{out.name} came out {stream['width']}x{stream['height']}"
    assert stream["pix_fmt"].startswith("yuva444p"), f"{out.name} has no alpha: {stream['pix_fmt']}"
    partial.rename(out)
    return {"file": str(out), "frames": frames, "size": list(size), "rate": format_rate(rate),
            "pix_fmt": stream["pix_fmt"], "tags": {key: stream.get(key) for key in
                                                   ("color_primaries", "color_transfer", "color_space", "color_range")}}


def straight(png: bytes, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """A captured page as float colour (its encoded codes, straight alpha) and alpha."""
    from PIL import Image

    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.float32) / np.float32(255)
    assert image.shape[1::-1] == tuple(size), f"the page is {image.shape[1]}x{image.shape[0]}, the comp {size}"
    return np.ascontiguousarray(image[..., :3]), np.ascontiguousarray(image[..., 3:])


def over(frame: np.ndarray, png: bytes) -> np.ndarray:
    """A captured page (straight alpha, 8-bit sRGB-encoded codes) drawn over a frame of
    display-encoded values: a display-referred layer's codes are the deliverable's codes
    (04-color.md, Graphics), composited on encoded values as the browser composites, which
    the spike measured within a code of the live page (bench/RESULTS.md, Phase 1 spike)."""
    from PIL import Image

    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.float32) / np.float32(255)
    assert image.shape[:2] == frame.shape[:2], f"the page is {image.shape[1]}x{image.shape[0]}, the frame is not"
    alpha = image[..., 3:]
    return np.ascontiguousarray(image[..., :3] * alpha + frame * (1 - alpha), dtype=np.float32)
