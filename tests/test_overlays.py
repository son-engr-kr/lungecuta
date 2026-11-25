"""Graphics captures kept in the project's cache and made by several Chromiums
(overlays.py; 05-motion-graphics.md, Captures)."""
import io
import time

import numpy as np
import pytest
from PIL import Image

from test_page_layers import MOVING, with_page
from lungecuta import graphics, overlays
from lungecuta.compose import frame_at

# A bar whose colour comes from a file the page loads
LOADED = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}
#b{position:absolute;top:20px;left:10px;width:40px;height:20px}
</style><script src="colour.js"></script></head><body><div id="b"></div><script>
const b = document.getElementById("b");
window.renderAt = (t) => { b.style.background = COLOUR; };
window.ready = true;
</script></body></html>"""


def no_chromium(monkeypatch):
    def refused():
        raise AssertionError("a Chromium was started for captures the cache holds")
    monkeypatch.setattr(graphics, "chromium", refused)


def pixels(png: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def test_a_frame_looked_at_again_takes_its_captures_from_the_cache(cut_project, monkeypatch):
    project, resolved = with_page(cut_project, MOVING)
    index = resolved.overlays[0].first + 3
    first = frame_at(project, resolved, index)
    no_chromium(monkeypatch)
    np.testing.assert_array_equal(frame_at(project, resolved, index), first)


def test_a_file_the_page_loads_changing_captures_it_again(cut_project):
    (cut_project / "graphics").mkdir(exist_ok=True)
    (cut_project / "graphics" / "colour.js").write_text('const COLOUR = "rgb(255, 0, 0)";')
    project, resolved = with_page(cut_project, LOADED)
    index = resolved.overlays[0].first
    red = frame_at(project, resolved, index)
    (cut_project / "graphics" / "colour.js").write_text('const COLOUR = "rgb(0, 0, 255)";')
    blue = frame_at(project, resolved, index)
    centre = (30, 30)
    assert red[centre][0] > red[centre][2] and blue[centre][2] > blue[centre][0]


def test_a_file_missing_when_the_page_asked_for_it_is_part_of_its_captures(cut_project, monkeypatch):
    project, resolved = with_page(cut_project, LOADED.replace("COLOUR;", "typeof COLOUR === 'undefined' ? 'lime' : COLOUR;"))
    index = resolved.overlays[0].first
    frame_at(project, resolved, index)
    (cut_project / "graphics" / "colour.js").write_text('const COLOUR = "rgb(0, 0, 255)";')
    blue = frame_at(project, resolved, index)  # captured again: the file appeared
    assert blue[30, 30][2] > blue[30, 30][1]
    no_chromium(monkeypatch)
    frame_at(project, resolved, index)


def test_several_chromiums_capture_what_one_does(cut_project, tmp_path):
    project, resolved = with_page(cut_project, MOVING)
    indices = range(resolved.overlays[0].first, resolved.overlays[0].end)
    (one,) = overlays.captures(project, resolved, indices, workers=1).values()
    alone = {path.name: pixels(path.read_bytes()) for path in one.directory.glob("*.png")}
    for path in one.directory.iterdir():
        path.unlink()
    one.directory.rmdir()
    (many,) = overlays.captures(project, resolved, indices, workers=3).values()
    together = {path.name: pixels(path.read_bytes()) for path in many.directory.glob("*.png")}
    assert len(alone) == len(indices) and alone.keys() == together.keys()
    for name in alone:
        np.testing.assert_array_equal(together[name], alone[name])


def test_a_page_that_throws_before_it_is_ready_fails_at_once(cut_project):
    project, resolved = with_page(cut_project, MOVING.replace("window.ready = true;",
                                                              "console.log('starting'); labels.top; window.ready = true;"))
    begun = time.monotonic()
    with pytest.raises(AssertionError, match=r"(?s)labels is not defined.*log: starting"):
        frame_at(project, resolved, resolved.overlays[0].first)
    assert time.monotonic() - begun < graphics.READY_TIMEOUT_MS / 1000 / 2
