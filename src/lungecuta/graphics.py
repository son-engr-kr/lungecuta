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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from urllib.parse import unquote, urlencode

from .encode import TAGS
from .files import content_hash, content_hash_text, write_atomic
from .ticks import format_rate
from .tools import run, tool

# The flags the capture measurements used (bench/graphics/README.md).
CHROMIUM_ARGS = ["--use-angle=metal", "--ignore-gpu-blocklist", "--enable-gpu-rasterization",
                 "--force-color-profile=srgb"]
READY_TIMEOUT_MS = 120_000  # what the past renderer waited for `window.ready`
CONSOLE_LINES = 40  # the end of a page's console that a failure reports
# Errors before the page is ready, which `pageerror` reports to Python but cannot end the
# wait for `window.ready` with: kept in the page, so the wait ends on the first.
_CATCH = """window.__lungecuta_errors = [];
addEventListener("error", e => window.__lungecuta_errors.push(String((e.error && e.error.stack) || e.message)));
addEventListener("unhandledrejection", e => window.__lungecuta_errors.push(String((e.reason && e.reason.stack) || e.reason)));"""
TO_YCBCR = "scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int"
# The frames carry their own colour properties into the encoder, so they are set on the frames.


def served_hash(path: Path) -> str | None:
    """What a page was given at `path`: the file's hash, a directory's listing, or None
    for nothing there (a page can depend on a file being absent)."""
    if path.is_file():
        return content_hash(path)
    if path.is_dir():
        return content_hash_text("\n".join(sorted(entry.name for entry in path.iterdir())))
    return None


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, photos: dict[str, Path], served: dict[Path, str | None] | None, **kwargs: Any):
        self.photos = photos
        self.served = served
        super().__init__(*args, **kwargs)

    def translate_path(self, path: str) -> str:
        """A page's own directory, and the photos it was given by media key (MEDIA_URL).
        Every file of the directory asked for is recorded in `served` with what it held
        when it was asked for, so a capture knows what it depends on (overlays.py)."""
        from .media import MEDIA_URL

        path = path.split("?", 1)[0]
        if path.startswith(MEDIA_URL):
            key = unquote(path.removeprefix(MEDIA_URL))
            return str(self.photos[key]) if key in self.photos else str(Path(self.directory) / ".missing")
        local = super().translate_path(path)
        if self.served is not None:
            self.served[Path(local)] = served_hash(Path(local))
        return local

    def log_message(self, format: str, *args: Any) -> None:
        pass


@contextmanager
def serve(directory: Path, photos: dict[str, Path] | None = None,
          served: dict[Path, str | None] | None = None) -> Iterator[str]:
    """Serve `directory` on 127.0.0.1 at a free port, and `photos` (files by media key) at
    MEDIA_URL; yields its origin. The files asked for go into `served` if it is given."""
    handler = functools.partial(_Quiet, directory=str(directory), photos=photos or {}, served=served)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def chromium() -> Iterator[Any]:
    """One headless Chromium for every page a render opens: Playwright's sync API runs one
    instance to a thread, so each page is a context of this browser, not a browser of its own."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        yield browser
        browser.close()


def page_query(params: dict[str, Any] | None, markers: Sequence[dict] = ()) -> str:
    """The query a page is opened with: its params, and its layer's markers when it has
    any (05-motion-graphics.md, A page's events)."""
    fields = ({"params": json.dumps(params)} if params else {}) | ({"markers": json.dumps(list(markers))} if markers else {})
    return "?" + urlencode(fields) if fields else ""


@contextmanager
def opened(browser: Any, page_file: Path, size: tuple[int, int], params: dict[str, Any] | None = None,
           photos: dict[str, Path] | None = None, resolution: Fraction = Fraction(1),
           served: dict[Path, str | None] | None = None, markers: Sequence[dict] = ()) -> Iterator[Any]:
    """A page of `size` CSS pixels in `browser` showing `page_file`, ready to render, in a
    context of its own (its own origin, viewport and request gate), captured at
    `resolution` device pixels a CSS pixel (which must make `size` whole pixels); `params`
    reach the page as JSON in its `params` query parameter, the layer's `markers` (each a
    name and `t` in seconds of layer time) as JSON in its `markers` one, `photos` are served to it by
    key, and the files it asks for go into `served`.

    A page that throws before it is ready fails at once with its error and its console,
    not after the wait for `window.ready` runs out."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeout

    page_file = page_file.resolve()
    assert page_file.is_file(), f"{page_file} is not a file"
    width, height = size
    assert (width * resolution).denominator == (height * resolution).denominator == 1, (
        f"{resolution} device pixels a CSS pixel does not make {width}x{height} whole pixels")
    with serve(page_file.parent, photos, served) as origin:
        context = browser.new_context(viewport={"width": width, "height": height},
                                      device_scale_factor=float(resolution))
        refused: list[str] = []

        def gate(route) -> None:
            url = route.request.url
            if url.startswith(origin + "/"):
                route.continue_()
            else:
                refused.append(url)
                route.abort()

        context.route("**/*", gate)
        # every step waits as long as a page's start may (a screenshot timed out at Playwright's
        # 30 s while the machine ran another job at a load of 38 on 15 cores)
        context.set_default_timeout(READY_TIMEOUT_MS)
        context.add_init_script(_CATCH)
        page = context.new_page()
        errors: list[str] = []
        console: list[str] = []
        page.on("pageerror", lambda error: errors.append(error.stack or str(error)))
        page.on("console", lambda message: console.append(f"{message.type}: {message.text}"))

        def failed() -> str:
            said = "\n".join(console[-CONSOLE_LINES:])
            return f"{page_file.name} raised: {errors[0]}" + (f"\nits console:\n{said}" if said else "")

        query = page_query(params, markers)
        page.goto(f"{origin}/{page_file.name}{query}")
        try:
            page.wait_for_function("window.ready === true || window.__lungecuta_errors.length > 0",
                                   timeout=READY_TIMEOUT_MS)
        except PlaywrightTimeout as timeout:
            said = "\n".join(console[-CONSOLE_LINES:]) or "(nothing)"
            raise AssertionError(f"{page_file.name} did not set window.ready = true in {READY_TIMEOUT_MS // 1000} s; "
                                 f"its console:\n{said}") from timeout
        errors += [error for error in page.evaluate("window.__lungecuta_errors") if error not in errors]
        assert not errors, failed()
        yield page
        assert not errors, failed()
        assert not refused, f"{page_file.name} asked for {refused[0]}, outside its directory; refused"
        context.close()


# renderAt, then what still moves on the clock: CSS animations and transitions, and Web
# Animations left playing (one paused and seeked by renderAt is `paused`). getAnimations
# flushes styles first, so a transition that renderAt's change of style starts is listed.
_RENDER = """async t => {
  await window.renderAt(t);
  return document.getAnimations().filter(a => a.playState === "running")
    .map(a => a.animationName || (a.transitionProperty && `transition of ${a.transitionProperty}`) || a.id || "an animation");
}"""


def frame_png(page: Any, t: float) -> bytes:
    """Frame `t` (seconds) as a PNG with a transparent background. A page with an animation
    running on the clock is refused: its capture would show the moment it was taken."""
    running = page.evaluate(_RENDER, t)
    assert not running, (f"{page.url.split('?')[0].rsplit('/', 1)[-1]} at {t:.3f} s: {', '.join(running)} runs on "
                         "the clock, not on renderAt(t), so a frame would show whenever it was captured; draw it from "
                         "t in renderAt (a Web Animation: pause() it and set its currentTime from t)")
    return page.screenshot(type="png", omit_background=True)


def render(page_file: Path, out: Path, *, size: tuple[int, int], rate: Fraction, frames: int,
           first_time: Fraction = Fraction(0), params: dict[str, Any] | None = None,
           photos: dict[str, Path] | None = None, markers: Sequence[dict] = ()) -> dict[str, Any]:
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
    with chromium() as browser, opened(browser, page_file, size, params, photos, markers=markers) as page:
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


def page_still(page_file: Path, out: Path, *, size: tuple[int, int], t: float, params: dict[str, Any] | None = None,
               under: np.ndarray | None = None, markers: Sequence[dict] = ()) -> dict[str, Any]:
    """One PNG of a page at page time `t` seconds: its capture, transparent where the page
    draws nothing; or over `under` (a display-encoded frame of `size`, such as a clip's
    `lungecuta still`), composited on display-encoded values as a render composites a top
    graphics layer (05-motion-graphics.md, A page at one time)."""
    from .images import png as png_bytes

    assert out.suffix == ".png", f"a page at one time is a .png, not {out.name}"
    with chromium() as browser, opened(browser, page_file, size, params, markers=markers) as page:
        captured = frame_png(page, t)
    out.parent.mkdir(parents=True, exist_ok=True)
    if under is None:
        write_atomic(out, captured)
    else:
        assert under.shape[1::-1] == tuple(size), f"the frame under the page is {under.shape[1]}x{under.shape[0]}, not {size}"
        rgb, alpha = straight(captured, size)
        write_atomic(out, png_bytes(np.ascontiguousarray(rgb * alpha + under * (1 - alpha), dtype=np.float32)))
    return {"png": str(out), "t": t, "size": list(size)}


def straight(png: bytes, size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray]:
    """A captured page as float colour (its encoded codes, straight alpha) and alpha."""
    from PIL import Image

    image = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.float32) / np.float32(255)
    assert image.shape[1::-1] == tuple(size), f"the page is {image.shape[1]}x{image.shape[0]}, the comp {size}"
    return np.ascontiguousarray(image[..., :3]), np.ascontiguousarray(image[..., 3:])

