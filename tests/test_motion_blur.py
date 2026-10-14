"""Motion blur of a layer's own movement over its shutter (06-editing.md, Motion blur)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import comp_json, put_comp
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.compose import frame_at, placed_layer, shutter_times
from lungecuta.media import Project
from lungecuta.ticks import ticks_per_frame

# shot1 slides right from the middle by two comp widths over one beat (0.5 s): 256 px in
# about 15 frames, 17.1 px a frame, so a 180 degree shutter smears it over about 8.5 px
SLIDE = {"position": {"keys": [{"t": "b:0", "v": [0.5, 0.5]}, {"t": "b:1", "v": [2.5, 0.5]}]}}


def resolved_with(project_dir, **layer):
    comp = comp_json()
    comp["layers"][1] |= layer
    put_comp(project_dir, comp)
    project = Project.load(project_dir)
    return project, resolve(project, load_comp(project, "main"))


def cut_of(resolved):
    return next(cut for cut in resolved.cuts if cut.layer.id == "shot1")


def test_a_still_layer_with_a_shutter_is_unchanged(cut_project):
    project, plain = resolved_with(cut_project, transform={"scale": 0.5})
    _, blurred = resolved_with(cut_project, transform={"scale": 0.5}, motion_blur={"shutter_deg": 180})
    assert len(shutter_times(cut_of(blurred), 20, (128, 72), (128, 72))) == 1
    np.testing.assert_allclose(frame_at(project, blurred, 20), frame_at(project, plain, 20), atol=1e-5)


def test_a_moving_edge_spreads_over_the_distance_the_shutter_covers(cut_project):
    project, resolved = resolved_with(cut_project, transform=SLIDE, motion_blur={"shutter_deg": 180})
    cut = cut_of(resolved)
    frame = 12  # inside the slide: frames 8 (b:0) to about 22 (b:1)
    times = shutter_times(cut, frame, (128, 72), (128, 72))
    tick = ticks_per_frame(cut.comp_rate)
    speed = 256 / (0.5 * 30000 / 1001)  # px a frame: 256 px over the 15 frames of half a second
    assert len(times) == pytest.approx(speed / 2, abs=1.01)  # one sample a pixel of travel
    assert times[0] > frame * tick and times[-1] < (frame + 0.5) * tick  # inside [t, t + half a frame]
    layer = np.ones((72, 128, 3), np.float32) * 0.4
    _, alpha = placed_layer(project, cut, frame, layer, (128, 72))
    row = alpha[36, :, 0]
    ramp = np.flatnonzero((row > 0.02) & (row < 0.98))
    assert len(ramp) == pytest.approx(speed / 2, abs=2)  # the left edge, smeared over the shutter's travel
    assert np.all(np.diff(row[ramp[0] - 1:ramp[-1] + 2]) >= -1e-6)  # rising across it, never back
    _, still = resolved_with(cut_project, transform=SLIDE)
    _, sharp = placed_layer(project, cut_of(still), frame, layer, (128, 72))
    assert np.flatnonzero((sharp[36, :, 0] > 0.02) & (sharp[36, :, 0] < 0.98)).size <= 1  # no shutter, no smear


def test_a_keyed_shutter_opens_only_where_its_keys_say(cut_project):
    # an ease shapes the segment arriving at its key: held at 0 from b:0, open from b:0.5
    shutter = {"keys": [{"t": "b:0", "v": 0}, {"t": "b:0.5", "v": 180, "ease": "hold"}]}
    _, resolved = resolved_with(cut_project, transform=SLIDE, motion_blur={"shutter_deg": shutter})
    cut = cut_of(resolved)
    assert len(shutter_times(cut, 9, (128, 72), (128, 72))) == 1  # held at 0 until b:0.5
    assert len(shutter_times(cut, 20, (128, 72), (128, 72))) > 4
    with pytest.raises(ValueError, match="a shutter of 800"):
        resolved_with(cut_project, motion_blur={"shutter_deg": 800})


def test_a_blurred_comp_renders_verifies_and_previews(cut_project):
    from lungecuta.preview import Preview

    resolved_with(cut_project, transform=SLIDE, motion_blur={"shutter_deg": 180})
    args = ["--project", str(cut_project)]
    rendered = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", *args])
    assert rendered.exit_code == 0, rendered.output
    checked = CliRunner().invoke(main, ["verify", json.loads(rendered.output)["file"], "--comp", "main",
                                        "--preset", "master-prores-hq", *args])
    assert checked.exit_code == 0, checked.output
    shown = Preview(Project.load(cut_project), "main").display(12)  # the editor draws the same blur, smaller
    row = shown[18].max(axis=1)
    assert np.flatnonzero((row > 0.01) & (row < row.max() * 0.98)).size >= 3


def test_fcpxml_refuses_motion_blur_rather_than_dropping_it(cut_project):
    resolved_with(cut_project, motion_blur={"shutter_deg": 180})
    refused = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert "motion blur, which is not written to FCPXML yet" in str(refused.exception)


def test_op_sets_and_keys_the_shutter(cut_project):
    def shot1():
        return next(layer for layer in json.loads((cut_project / "comps" / "main.json").read_text())["layers"]
                    if layer["id"] == "shot1")

    args = ["--project", str(cut_project)]
    for command in (["set", "main", "shot1", "shutter", "--value", "180"],
                    ["key", "main", "shot1", "shutter", "--at", "b:1", "--value", "90"],
                    ["key", "main", "shot1", "shutter", "--at", "b:2", "--value", "0"]):
        result = CliRunner().invoke(main, ["op", *command, *args])
        assert result.exit_code == 0, result.output
    assert shot1()["motion_blur"] == {"shutter_deg": {"keys": [{"t": "b:1", "v": 90.0}, {"t": "b:2", "v": 0.0}]}}
    for at in ("b:1", "b:2"):
        CliRunner().invoke(main, ["op", "unkey", "main", "shot1", "shutter", "--at", at, *args])
    assert shot1()["motion_blur"] == {"shutter_deg": 0.0}  # the last key's value stays, as a transform's does
    result = CliRunner().invoke(main, ["op", "set", "main", "shot1", "shutter", "--value", "none", *args])
    assert result.exit_code == 0, result.output
    assert "motion_blur" not in shot1()
