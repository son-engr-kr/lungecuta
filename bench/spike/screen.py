"""Phase 1 spike: on-screen colour and graphics parity, in the pinned Chromium.

Usage: uv run --with aiohttp,av python bench/spike/screen.py PROJECT KEY
  (inside the project)

On-screen colour: a 960x540 frame of known 8-bit sRGB codes (every code of each channel as
ramps, a 16 x 16 x 16 sample of the RGB cube, and seeded random colours) is drawn in the
viewer with putImageData and captured with a page screenshot; and a real engine frame
(the comp frame of latency.py at frame 352) the same way. Criterion: within 1 code
(*proposed*, docs/design/10-roadmap.md, Phase 1).

Graphics parity: a title page (gradient-clipped text, a translucent rounded card with a
shadow, a bar), captured on a transparent background as `lungecuta graphic` captures it and
composited over the engine frame in numpy (straight alpha, on the 8-bit sRGB-encoded
values, as a top overlay is composited on display-encoded values), against the same page
live in a transparent iframe over the same frame, screenshotted. Criterion: p99 within
2 codes (*proposed*). Measured at three times of the title's animation.
"""
import base64
import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import Engine  # noqa: E402
from server import start  # noqa: E402

from lungecuta.graphics import CHROMIUM_ARGS, serve  # noqa: E402

HERE = Path(__file__).resolve().parent
W, H = 960, 540


def pattern() -> np.ndarray:
    frame = np.zeros((H, W, 3), np.uint8)
    ramp = np.arange(256, dtype=np.uint8)
    for c in range(3):  # rows 0 to 29: each channel's 256 codes, then all three together
        frame[c * 10:(c + 1) * 10, :256, c] = ramp
        frame[30:40, :256] = ramp[:, None]
    cube = np.stack(np.meshgrid(*(np.arange(0, 256, 17, dtype=np.uint8),) * 3, indexing="ij"), -1).reshape(-1, 3)
    frame[40:40 + 16, :256] = cube[:4096].reshape(16, 256, 3)
    rng = np.random.default_rng(0)
    frame[60:] = rng.integers(0, 256, (H - 60, W, 3), dtype=np.uint8)
    return frame


def rgba(frame: np.ndarray) -> str:
    out = np.empty((H, W, 4), np.uint8)
    out[..., :3], out[..., 3] = frame, 255
    return base64.b64encode(out.tobytes()).decode()


def shot(page) -> np.ndarray:
    png = page.screenshot(type="png", clip={"x": 0, "y": 0, "width": W, "height": H})
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


def report(name: str, expected: np.ndarray, got: np.ndarray, limit: float, statistic: str, mask=None) -> None:
    diff = np.abs(expected.astype(np.int16) - got.astype(np.int16))
    if mask is not None:  # only where the title covers the frame
        diff = diff[mask[..., 0]]
    p99 = float(np.percentile(diff, 99))
    value = diff.max() if statistic == "max" else p99
    print(f"  {name}: max {diff.max()}, p99 {p99:.2f}, mean {diff.mean():.4f}, share differing {np.mean(diff > 0):.5f}"
          f"  -> {'within' if value <= limit else 'OUTSIDE'} {limit} codes ({statistic})")


def main() -> None:
    from playwright.sync_api import sync_playwright

    project, key = Path(sys.argv[1]), sys.argv[2]
    engine = Engine(project, key)
    url, _ = start(engine)
    data, _ = engine.render(352, 452, 0.2, 1.1, cached=False)
    background = np.frombuffer(data, np.uint8).reshape(H, W, 4)[..., :3].copy()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        page.goto(url)
        page.evaluate("window.ready")
        print(f"on-screen colour, Chromium {browser.version}:")
        for name, frame in (("known codes", pattern()), ("engine frame", background)):
            page.evaluate(f"window.show({W}, {H}, '{rgba(frame)}')")
            report(name, frame, shot(page), 1, "max")
        with serve(HERE) as origin:
            title = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            title.goto(f"{origin}/title.html")
            title.wait_for_function("window.ready === true")
            live = browser.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
            live.goto(f"{origin}/overlay.html")
            live.evaluate("window.ready")
            print("graphics parity, a title captured and composited against the same title live:")
            for t in (0.3, 0.6, 2.0):
                title.evaluate(f"window.renderAt({t})")
                png = title.screenshot(type="png", omit_background=True)
                fg = np.asarray(Image.open(io.BytesIO(png)).convert("RGBA")).astype(np.float64)
                alpha = fg[..., 3:] / 255
                composited = np.floor(fg[..., :3] * alpha + background * (1 - alpha) + 0.5).astype(np.uint8)
                live.evaluate(f"window.showAt('{rgba(background)}', {t})")
                report(f"t = {t} s, over the {int((alpha > 0).sum())} pixels the title covers", composited, shot(live), 2,
                       "p99", mask=alpha > 0)
        browser.close()


if __name__ == "__main__":
    main()
