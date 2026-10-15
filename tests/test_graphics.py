"""Web pages to ProRes 4444 with alpha, and the patch card read back (Phase 0 task 7)."""
import json
from fractions import Fraction

import numpy as np
import pytest

from lungecuta.graphics import render
from lungecuta.patches import card, patch_list, pixel_rect, read_patches, write_card
from lungecuta.tools import run

PAGE = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="64" height="36"></canvas><script>
const ctx = document.getElementById("c").getContext("2d");
window.renderAt = (t) => {
  ctx.clearRect(0, 0, 64, 36);
  const v = Math.round(t * 25) * 40;  // frame n draws grey 40 n on the left half
  ctx.fillStyle = `rgb(${v}, ${v}, ${v})`;
  ctx.fillRect(0, 0, 32, 36);
};
window.ready = true;
</script></body></html>"""


def decode(path, pix_fmt):
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"]).stdout
    return raw


def test_a_page_renders_frame_by_frame_with_alpha(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(PAGE)
    out = tmp_path / "out.mov"
    result = render(page, out, size=(64, 36), rate=Fraction(25), frames=3)
    assert result["frames"] == 3 and result["pix_fmt"].startswith("yuva444p")
    assert result["tags"] == {"color_primaries": "bt709", "color_transfer": "bt709", "color_space": "bt709",
                              "color_range": "tv"}
    frames = np.frombuffer(decode(out, "rgba"), np.uint8).reshape(3, 36, 64, 4)
    assert [int(frames[n, 18, 8, 0]) for n in range(3)] == [0, 40, 80], "frame n is drawn at t = n / rate"
    assert np.all(frames[:, :, :32, 3] == 255) and np.all(frames[:, :, 40:, 3] == 0), "transparent where nothing is drawn"


def test_a_page_reaching_outside_its_directory_is_refused(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(PAGE.replace("window.ready = true;", "fetch('https://example.com/x.js').catch(() => {}); window.ready = true;"))
    with pytest.raises(AssertionError, match="outside its directory; refused"):
        render(page, tmp_path / "out.mov", size=(64, 36), rate=Fraction(25), frames=1)


def test_a_page_that_throws_stops_the_render(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(PAGE.replace("ctx.clearRect(0, 0, 64, 36);", "throw new Error('broken frame');"))
    with pytest.raises(Exception, match="broken frame"):
        render(page, tmp_path / "out.mov", size=(64, 36), rate=Fraction(25), frames=1)


MOVING = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}
#b{position:absolute;top:0;left:0;width:8px;height:8px;background:#fff}STYLE</style></head><body><div id="b"></div><script>
const b = document.getElementById("b");
SCRIPT
window.ready = true;
</script></body></html>"""


@pytest.mark.parametrize("style, script, named", [
    ("#b{animation:go 1s linear infinite}@keyframes go{to{left:40px}}", "window.renderAt = (t) => {};", "go"),
    ("#b{transition:left .5s}", "window.renderAt = (t) => { b.style.left = `${t * 40}px`; };", "transition of left"),
    ("", "const a = b.animate([{left: '0px'}, {left: '40px'}], 1000);\nwindow.renderAt = (t) => {};", "an animation"),
])
def test_an_animation_running_on_the_clock_is_refused(tmp_path, style, script, named):
    page = tmp_path / "page.html"
    page.write_text(MOVING.replace("STYLE", style).replace("SCRIPT", script))
    with pytest.raises(AssertionError, match=f"{named} runs on the clock, not on renderAt"):
        render(page, tmp_path / "out.mov", size=(64, 36), rate=Fraction(25), frames=2)


def test_a_web_animation_seeked_by_render_at_is_drawn_at_t(tmp_path):
    page = tmp_path / "page.html"
    page.write_text(MOVING.replace("STYLE", "").replace("SCRIPT", (
        "const a = b.animate([{left: '0px'}, {left: '40px'}], 1000); a.pause();\n"
        "window.renderAt = (t) => { a.currentTime = t * 1000; };")))
    out = tmp_path / "out.mov"
    render(page, out, size=(64, 36), rate=Fraction(25), frames=3)
    frames = np.frombuffer(decode(out, "rgba"), np.uint8).reshape(3, 36, 64, 4)
    assert [int(np.flatnonzero(frames[n, 4, :, 3] > 128)[0]) for n in range(3)] == [0, 2, 3]  # 40 px a second at 25 fps


def test_the_card_holds_every_patch_inside_the_frame():
    spec = card((1920, 1080))
    assert len(spec["patches"]) == len(patch_list()) == 27
    names = [patch["name"] for patch in spec["patches"]]
    assert len(set(names)) == len(names)
    for patch in spec["patches"]:
        left, top, right, bottom = patch["rect"]
        assert 0 < left < right < 1 and 0 < top < bottom < 1
    assert {tuple(p["rgb"]) for p in spec["patches"]} >= {(255, 0, 0), (191, 191, 0), (128, 128, 128), (235, 235, 235)}
    x0, y0, x1, y1 = pixel_rect(spec["patches"][0]["rect"], (1920, 1080))
    assert x1 > x0 and y1 > y0


def test_the_card_file_reads_back_with_every_patch_code(tmp_path):
    result = write_card(tmp_path / "card", size=(480, 270), rate=Fraction(25), frames=2)
    assert result["frames"] == 2 and result["patches"] == 27
    spec = json.loads((tmp_path / "card" / "card.json").read_text())
    read = read_patches(tmp_path / "card" / "card.mov", spec, index=1, matrix=None, range_=None)
    assert read["largest_difference"] < 0.5
    assert all(round(m) == e for patch in read["patches"] for m, e in zip(patch["measured"], patch["expected"]))
    with pytest.raises(AssertionError, match="exists already"):
        write_card(tmp_path / "card", size=(480, 270), rate=Fraction(25), frames=2)
