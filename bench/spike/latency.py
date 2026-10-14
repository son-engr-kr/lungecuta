"""Phase 1 spike: interaction latency, from a change in the viewer to its pixels on screen.

Usage: uv run --with aiohttp,av python bench/spike/latency.py PROJECT KEY
  (inside the project; PROJECT holds the media record KEY with its FFV1 proxy)

The viewer (viewer.html, in the pinned Chromium, headless, through Playwright) sends a
change, the engine (engine.py) renders the 960x540 comp frame (two layers of the clip's
proxy, a dynamic OCIO grade on the top one, a 50 % blend, the output view, sRGB for the
viewer), the bytes come back over a WebSocket and are drawn with putImageData; the time
runs to the next animation frame after the draw. Changes go one after another. Modes:
  parked     a grade change on a frame already decoded and through its input transform
  scrub      a new frame each time: both layers decoded (PyAV, decoder kept open) first
  scrub-cli  the same with one ffmpeg process per decoded frame, as lungecuta still reads
Checked: every reply is a whole 960x540 RGBA frame; the frame drawn is read back from the
canvas for the last change of each mode and must equal the bytes received.
Criterion (docs/design/10-roadmap.md, Phase 1): p95 at most 150 ms at 540p (*proposed*).
"""
import os
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import Engine  # noqa: E402
from server import start  # noqa: E402

from lungecuta.graphics import CHROMIUM_ARGS  # noqa: E402

RUNS = {"parked": 200, "scrub": 100, "scrub-cli": 30}


def summary(name: str, results: list[dict]) -> None:
    total = np.array([r["total"] for r in results])
    stages = {}
    for r in results:
        for key, value in r["stages"].items():
            stages.setdefault(key, []).append(value)
    parts = ", ".join(f"{k} {statistics.median(v):.1f}" for k, v in stages.items())
    print(f"  {name:9s} n={len(results)}: total p50 {np.percentile(total, 50):.1f} ms, p95 {np.percentile(total, 95):.1f}, "
          f"max {total.max():.1f}; engine p50 {statistics.median(r['engine'] for r in results):.1f}, "
          f"transport p50 {statistics.median(r['transport'] for r in results):.1f}, "
          f"draw to next frame p50 {statistics.median(r['draw'] for r in results):.1f}")
    print(f"            engine stages, median ms: {parts}")


def main() -> None:
    from playwright.sync_api import sync_playwright

    project, key = Path(sys.argv[1]), sys.argv[2]
    engine = Engine(project, key, threads=os.cpu_count())
    url, _ = start(engine)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 960, "height": 540}, device_scale_factor=1)
        page.goto(url)
        page.evaluate("window.ready")
        page.evaluate("window.run(10, 'parked')")  # warm up
        print(f"{key} proxy, {engine.size[0]}x{engine.size[1]}, Chromium {browser.version}, OCIO threads {os.cpu_count()}")
        for mode, n in RUNS.items():
            results = page.evaluate(f"window.run({n}, '{mode}')")
            summary(mode, results)
            # The canvas holds what the engine sent for the last change.
            drawn = page.evaluate("Array.from(document.getElementById('view').getContext('2d')"
                                  ".getImageData(0, 0, 960, 540).data.slice(0, 960 * 4 * 8))")
            last_frame = (37 * (n - 1)) % 600 if mode.startswith("scrub") else 352
            data, _ = engine.render(last_frame, last_frame + 100, 0.01 * ((n - 1) % 50), 1.0 + 0.002 * ((n - 1) % 50),
                                    cached=False, decoder="cli" if mode == "scrub-cli" else "pyav")
            assert bytes(drawn) == data[:960 * 4 * 8], f"{mode}: the canvas does not hold the frame sent"
        browser.close()


if __name__ == "__main__":
    main()
