"""Whether a graphics page's captures move as its `renderAt(t)` says, through lungecuta's
own capture (`lungecuta.graphics`: one Chromium, a context a page, seek then screenshot).

Two measurements:

- stale frames: a block drawn at x = 8 + 4 n (whole pixels) on frame n, by DOM `left`, a
  CSS transform, Canvas2D and WebGL, captured in order and in a shuffled order, all pages
  open at once in one browser and captured in turn as a render does; a capture whose block
  is not where frame n puts it showed another frame;
- slow movement: a word moved 0.25 px a frame, by DOM `left`, a CSS transform, a CSS
  transform with `will-change`, and Canvas2D `fillText`; the alpha-weighted centre of
  the word per frame, whose step should be 0.25 px each frame. A step that does not move
  is movement snapped to a grid, which shows as judder;
- slow zoom: a word scaled from 1 to 1.2 over the frames by a CSS transform, with and
  without `will-change`: the step of its alpha-weighted spread per frame, and how far the
  last frame's alpha is from the word drawn at 1.2 still (a layer rasterised once and
  scaled up is softer);
- fonts: a word in a font of the page's directory that the page does not wait for, and
  the same word after `document.fonts.load`, the first capture against one taken once the
  font has loaded.

Usage: uv run python bench/graphics_motion.py OUT_DIR
"""
import json
import random
import sys
from pathlib import Path

import numpy as np

from lungecuta.graphics import chromium, frame_png, opened, straight

SIZE = (320, 96)
FRAMES = 48
RATE = 24

HEAD = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:100%;height:100%;background:transparent;overflow:hidden}
#b{position:absolute;top:24px;left:0;width:16px;height:48px;background:#fff}
#w{position:absolute;top:20px;left:0;font:600 40px/1 Helvetica,Arial,sans-serif;color:#fff;white-space:nowrap}
</style></head><body>"""

BLOCKS = {
    "dom-left": '<div id="b"></div><script>const b=document.getElementById("b");'
                'window.renderAt=t=>{b.style.left=(8+4*Math.round(t*24))+"px"};',
    "dom-transform": '<div id="b"></div><script>const b=document.getElementById("b");'
                     'window.renderAt=t=>{b.style.transform=`translateX(${8+4*Math.round(t*24)}px)`};',
    "canvas2d": '<canvas id="c" width="320" height="96"></canvas><script>const c=document.getElementById("c").getContext("2d");'
                'window.renderAt=t=>{c.clearRect(0,0,320,96);c.fillStyle="#fff";c.fillRect(8+4*Math.round(t*24),24,16,48)};',
    "webgl": '<canvas id="c" width="320" height="96"></canvas><script>const g=document.getElementById("c").getContext("webgl2");'
             'g.enable(g.SCISSOR_TEST);window.renderAt=t=>{g.scissor(0,0,320,96);g.clearColor(0,0,0,0);g.clear(g.COLOR_BUFFER_BIT);'
             'g.scissor(8+4*Math.round(t*24),24,16,48);g.clearColor(1,1,1,1);g.clear(g.COLOR_BUFFER_BIT)};',
}

WORDS = {
    "dom-left": '<div id="w">Headline</div><script>const w=document.getElementById("w");'
                'window.renderAt=t=>{w.style.left=(20+0.25*Math.round(t*24))+"px"};',
    "dom-transform": '<div id="w">Headline</div><script>const w=document.getElementById("w");'
                     'window.renderAt=t=>{w.style.transform=`translateX(${20+0.25*Math.round(t*24)}px)`};',
    "dom-transform-will-change": '<div id="w" style="will-change:transform">Headline</div><script>'
                                 'const w=document.getElementById("w");'
                                 'window.renderAt=t=>{w.style.transform=`translateX(${20+0.25*Math.round(t*24)}px)`};',
    "canvas2d": '<canvas id="c" width="320" height="96"></canvas><script>const c=document.getElementById("c").getContext("2d");'
                'c.font="600 40px Helvetica, Arial, sans-serif";c.textBaseline="top";'
                'window.renderAt=t=>{c.clearRect(0,0,320,96);c.fillStyle="#fff";c.fillText("Headline",20+0.25*Math.round(t*24),20)};',
}


ZOOMS = {
    "dom-transform": '<div id="w" style="left:60px">Headline</div><script>const w=document.getElementById("w");'
                     'window.renderAt=t=>{w.style.transform=`scale(${1+0.2*Math.round(t*24)/47})`};',
    "dom-transform-will-change": '<div id="w" style="left:60px;will-change:transform">Headline</div><script>'
                                 'const w=document.getElementById("w");'
                                 'window.renderAt=t=>{w.style.transform=`scale(${1+0.2*Math.round(t*24)/47})`};',
    "still-at-1.2": '<div id="w" style="left:60px;transform:scale(1.2)">Headline</div><script>window.renderAt=t=>{};',
}

FONT_FACE = '<style>@font-face{font-family:Bench;src:url(bench-font.ttf)}#w{font-family:Bench}</style>'
FONTS = {
    "not-awaited": FONT_FACE + '<div id="w">Headline</div><script>window.renderAt=t=>{};',
    "awaited": FONT_FACE + '<div id="w">Headline</div><script>window.renderAt=t=>{};'
               'document.fonts.load("600 40px Bench").then(()=>{window.ready=true});',
    # shown first by renderAt, as a title that comes in later does
    "first-used-later": FONT_FACE + '<div id="w" style="display:none">Headline</div><script>'
                        'const w=document.getElementById("w");window.renderAt=t=>{w.style.display="block"};',
}
# A face unlike the page's fallback (Helvetica), read from the system into the bench's own directory
FONT_FILE = Path("/System/Library/Fonts/Supplemental/Arial Black.ttf")


def page(directory: Path, name: str, body: str) -> Path:
    path = directory / f"{name}.html"
    ready = "" if "window.ready=true" in body else "\nwindow.ready=true;"
    path.write_text(HEAD + body + ready + "</script></body></html>")
    return path


def alpha(png: bytes) -> np.ndarray:
    return straight(png, SIZE)[1][..., 0]


def block_left(png: bytes) -> int:
    columns = np.flatnonzero(alpha(png)[48] > 0.5)
    assert len(columns) == 16, f"the block is {len(columns)} px wide"
    return int(columns[0])


def centre(png: bytes) -> float:
    a = alpha(png).astype(np.float64)
    assert a.sum() > 100, "the word is not drawn"
    return float((a.sum(axis=0) * np.arange(SIZE[0])).sum() / a.sum())


def spread_of(png: bytes) -> float:
    """The alpha-weighted standard deviation of the word's columns: its width, smoothly."""
    a = alpha(png).astype(np.float64).sum(axis=0)
    x = np.arange(SIZE[0])
    mean = (a * x).sum() / a.sum()
    return float(np.sqrt((a * (x - mean) ** 2).sum() / a.sum()))


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    order = list(range(FRAMES))
    shuffled = random.Random(0).sample(order, FRAMES)
    result: dict = {"size": list(SIZE), "frames": FRAMES, "rate": RATE, "stale": {}, "slow": {}}
    with chromium() as browser:
        version = browser.version
        pages = {name: opened(browser, page(out, f"block-{name}", body), SIZE).__enter__() for name, body in BLOCKS.items()}
        for label, frames in (("in order", order), ("shuffled", shuffled)):
            wrong = {name: [] for name in pages}
            for n in frames:  # every page captured in turn on each frame, as a render captures its layers
                for name, p in pages.items():
                    left = block_left(frame_png(p, n / RATE))
                    if left != 8 + 4 * n:
                        wrong[name].append({"frame": n, "showed": (left - 8) / 4})
            result["stale"][label] = {name: {"captured": len(frames), "wrong": len(w), "first": w[:3]}
                                      for name, w in wrong.items()}
        for name, body in WORDS.items():
            with opened(browser, page(out, f"word-{name}", body), SIZE) as p:
                centres = np.array([centre(frame_png(p, n / RATE)) for n in order])
            steps = np.diff(centres)
            result["slow"][name] = {
                "moved_px": round(float(centres[-1] - centres[0]), 3), "expected_px": 0.25 * (FRAMES - 1),
                "step_mean": round(float(steps.mean()), 4), "step_min": round(float(steps.min()), 4),
                "step_max": round(float(steps.max()), 4),
                "steps_still": int((np.abs(steps) < 0.02).sum()), "steps": len(steps)}
        for name, body in ZOOMS.items():
            with opened(browser, page(out, f"zoom-{name}", body), SIZE) as p:
                shots = [frame_png(p, n / RATE) for n in order]
            spread = np.array([spread_of(png) for png in shots])
            result.setdefault("zoom", {})[name] = {
                "spread_first": round(float(spread[0]), 3), "spread_last": round(float(spread[-1]), 3),
                "steps_still": int((np.abs(np.diff(spread)) < 0.005).sum()), "steps": len(spread) - 1,
                "step_min": round(float(np.diff(spread).min()), 4), "step_max": round(float(np.diff(spread).max()), 4),
                "last": alpha(shots[-1])}
        reference = result["zoom"].pop("still-at-1.2")["last"]
        for row in result["zoom"].values():
            row["last_from_still_mean"] = round(float(np.abs(row.pop("last") - reference).mean()), 5)
        (out / "bench-font.ttf").write_bytes(FONT_FILE.read_bytes())
        for name, body in FONTS.items():
            with opened(browser, page(out, f"font-{name}", body), SIZE) as p:
                first = frame_png(p, 0)
                p.evaluate("document.fonts.ready")
                later = frame_png(p, 0)
            result.setdefault("fonts", {})[name] = {
                "first_capture_differs": bool(np.abs(alpha(first) - alpha(later)).max() > 0)}
        assert result["fonts"]["awaited"]["first_capture_differs"] is False
    result["chromium"] = version
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
