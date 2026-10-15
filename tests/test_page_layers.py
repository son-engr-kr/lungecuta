"""Graphics layers placed, masked and blurred as footage is (05-motion-graphics.md, A page
as a layer): the comp in tests/clips.py, 128x72 at 29.97 fps, with pages over it."""
import dataclasses
import json
from fractions import Fraction

import numpy as np
import pytest
from click.testing import CliRunner

from clips import TITLE, comp_json, put_comp
from lungecuta import ops
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.comp.parts import capture_resolution
from lungecuta.compose import captured, frame_at, overlay_pages, page_layer, shutter_times
from lungecuta.media import Project
from lungecuta.schema.comp import GraphicsLayer
from lungecuta.ticks import ticks_per_frame

CIRCLE = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}
#c{position:absolute;left:LEFTpx;top:TOPpx;width:SIZEpx;height:SIZEpx;border-radius:50%;background:#fff}
</style></head><body><div id="c"></div><script>window.renderAt = (t) => {}; window.ready = true;</script></body></html>"""

# A bar 8 px wide that moves 240 px a second across the page on its own
MOVING = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}
#b{position:absolute;top:20px;left:0;width:8px;height:20px;background:#fff;will-change:transform}
</style></head><body><div id="b"></div><script>
const b = document.getElementById("b");
window.renderAt = (t) => { b.style.transform = `translateX(${10 + 240 * t}px)`; };
window.ready = true;
</script></body></html>"""


def circle(diameter: int) -> str:
    return (CIRCLE.replace("LEFT", str(64 - diameter / 2)).replace("TOP", str(36 - diameter / 2))
            .replace("SIZE", str(diameter)))


def with_page(project_dir, page: str, **layer):
    (project_dir / "graphics" / "page.html").write_text(page)
    comp = comp_json()
    comp["layers"].append({"id": "page", "type": "graphics", "page": "graphics/page.html", "space": "display",
                           "start": "b:2", "out": "b:6", **layer})
    put_comp(project_dir, comp)
    project = Project.load(project_dir)
    return project, resolve(project, load_comp(project, "main"))


def page_alone(project, resolved, index):
    """The graphics layer on frame `index`, premultiplied, and its alpha, as the top of the stack."""
    (overlay,) = resolved.overlays
    with overlay_pages(project, resolved) as pages:
        shots = captured(resolved, index, pages)["page"]
    return shots, page_layer(project, overlay, shots, tuple(resolved.comp.size), None, inline=False)


def test_a_graphics_layer_is_placed_by_its_transform(cut_project):
    project, resolved = with_page(cut_project, TITLE, params={"colour": "rgb(200, 40, 120)"},
                                  transform={"scale": 0.5, "position": [0.25, 0.25]})
    frame = frame_at(project, resolved, 60)
    beneath = frame_at(project, dataclasses.replace(resolved, overlays=[]), 60)
    # the page's box (4..44, 4..24) at half size about the page's middle, which lands at (32, 18)
    np.testing.assert_allclose(frame[7, 12] * 255, [200, 40, 120], atol=1e-3)
    np.testing.assert_array_equal(frame[30:36, 2:22], beneath[30:36, 2:22])  # where the box was, unplaced
    np.testing.assert_array_equal(frame[40:, 70:], beneath[40:, 70:])  # outside the page as placed


def test_a_page_scaled_up_is_captured_at_the_size_it_is_seen(cut_project):
    project, resolved = with_page(cut_project, circle(20), transform={"scale": 2})
    assert resolved.overlays[0].resolution == 2
    scaled = frame_at(project, resolved, 60)
    project, resolved = with_page(cut_project, circle(40))
    native = frame_at(project, resolved, 60)
    np.testing.assert_allclose(scaled, native, atol=1e-6)  # drawn at 40 px, not a 20 px circle enlarged
    project, resolved = with_page(cut_project, circle(20), transform={"scale": 2}, resolution=1)
    enlarged = frame_at(project, resolved, 60)
    assert np.abs(enlarged - native).max() > 0.05


def test_the_capture_resolution_makes_whole_pixels():
    layer = lambda **fields: GraphicsLayer.model_validate(
        {"id": "t", "type": "graphics", "page": "graphics/t.html", "space": "display", "start": "0s", "out": "1s",
         **fields})
    zoom = {"scale": {"keys": [{"t": "0s", "v": 1}, {"t": "1s", "v": [1.1, 1.05]}]}}
    assert capture_resolution(layer(transform=zoom), (1920, 1080)) == Fraction(11, 10)
    assert capture_resolution(layer(transform=zoom), (128, 72)) == Fraction(9, 8)  # raised to a step of 1/8
    assert capture_resolution(layer(transform={"scale": 0.5}), (128, 72)) == 1
    assert capture_resolution(layer(transform=zoom, resolution=1), (1920, 1080)) == 1
    assert capture_resolution(layer(), (1920, 1080)) == 1


def test_a_page_moving_on_its_own_is_blurred_over_the_shutter(cut_project):
    project, resolved = with_page(cut_project, MOVING)
    _, (_, sharp) = page_alone(project, resolved, 40)  # 0.085 s into the layer: the bar at x = 30
    project, resolved = with_page(cut_project, MOVING, motion_blur={"shutter_deg": 180, "samples": 8})
    shots, (_, blurred) = page_alone(project, resolved, 40)
    tick = ticks_per_frame(Fraction(30000, 1001))
    assert len(shots) == 8 and all(40 * tick < ticks < 40.5 * tick for ticks, _ in shots)
    row_sharp, row_blurred = sharp[30, :, 0], blurred[30, :, 0]
    # the bar moves 4 px over a half-frame shutter: its area kept, its edges spread over the move
    assert row_blurred.sum() == pytest.approx(row_sharp.sum(), abs=0.05)
    partial = lambda row: int(((row > 0.02) & (row < 0.98)).sum())
    assert partial(row_sharp) <= 2 and partial(row_blurred) >= 6


def test_a_graphics_layer_moved_fast_by_its_transform_is_blurred_along_its_path(cut_project):
    moving = {"position": {"keys": [{"t": "0s", "v": [0.3, 0.5]}, {"t": "0.2s", "v": [0.7, 0.5]}]}}
    project, resolved = with_page(cut_project, circle(10), transform=moving, motion_blur={"shutter_deg": 180})
    (overlay,) = resolved.overlays
    index = overlay.first + 2
    # 51.2 px in 0.2 s is 8.5 px a frame, 4.3 over the shutter: a sample for every pixel
    assert len(shutter_times(overlay, index, (128, 72), (128, 72))) == 5
    _, (_, alpha) = page_alone(project, resolved, index)
    assert int(((alpha[36, :, 0] > 0.02) & (alpha[36, :, 0] < 0.98)).sum()) >= 6


def test_a_graphics_layer_is_cut_by_its_masks(cut_project):
    left = [{"shape": "rectangle", "centre": [0.25, 0.5], "size": [0.5, 1.0]}]
    project, resolved = with_page(cut_project, TITLE, params={"colour": "rgb(200, 40, 120)"}, masks=left)
    frame = frame_at(project, resolved, 60)
    beneath = frame_at(project, dataclasses.replace(resolved, overlays=[]), 60)
    np.testing.assert_allclose(frame[10, 10] * 255, [200, 40, 120], atol=1e-3)
    np.testing.assert_array_equal(frame[45:55, 70:100], beneath[45:55, 70:100])  # the fading box, masked off


def test_graphics_and_precomp_layers_take_keys_masks_and_motion_blur(cut_project):
    (cut_project / "graphics" / "title.html").write_text(TITLE)
    project = Project.load(cut_project)
    ops.graphic(project, "main", "graphics/title.html", "b:2", "b:4", {"colour": "#ff0000"})
    ops.key(project, "main", "title", "scale", "b:2", 1.0)
    ops.key(project, "main", "title", "scale", "b:6", 1.2, "in_out")
    ops.set_value(project, "main", "title", "shutter", 180.0)
    ops.set_value(project, "main", "title", "samples", 4)
    ops.set_masks(project, "main", "title", [{"shape": "ellipse", "size": [0.8, 0.8], "feather": 0.05}])
    layer = json.loads((cut_project / "comps" / "main.json").read_text())["layers"][-1]
    assert layer["transform"] == {"scale": {"keys": [{"t": "b:0", "v": 1.0}, {"t": "b:4", "v": 1.2, "ease": "in_out"}]}}
    assert layer["motion_blur"] == {"shutter_deg": 180.0, "samples": 4} and len(layer["masks"]) == 1
    (overlay,) = resolve(project, load_comp(project, "main")).overlays
    assert overlay.resolution == Fraction(5, 4) and overlay.samples == 4  # 1.2 raised to a step of 1/8
    with pytest.raises(AssertionError, match="remap is a property of footage layers, and title is a graphics layer"):
        ops.key(project, "main", "title", "remap", "b:2", "0s")
    with pytest.raises(AssertionError, match="samples is a property of graphics layers, and shot1 is a footage layer"):
        ops.set_value(project, "main", "shot1", "samples", 4)
    refused = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert refused.exit_code != 0 and "has a transform, masks or motion blur" in str(refused.exception)
    from test_precomp import with_precomp

    project, _ = with_precomp(cut_project)
    ops.key(project, "main", "shot1", "position", "b:1", [0.4, 0.5])
    assert json.loads((cut_project / "comps" / "main.json").read_text())["layers"][1]["transform"] == {
        "position": {"keys": [{"t": "b:1", "v": [0.4, 0.5]}]}}
