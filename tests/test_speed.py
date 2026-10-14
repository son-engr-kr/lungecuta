"""lungecuta op speed: a range of a footage layer, or one segment of its remap, at one speed
(src/lungecuta/ops/properties.py), on the comp of tests/clips.py: beats every 0.5 s from
0.25 s, shot1 at 100 % from b:0 to b:4, shot2 at 50 % from b:4 to b:8 from source 0 s."""
import json

import pytest
from click.testing import CliRunner

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.literals import TICKS_PER_SECOND
from lungecuta.media import Project
from lungecuta.ticks import ticks_per_frame


def cut(project_dir, layer_id):
    project = Project.load(project_dir)
    return {c.layer.id: c for c in resolve(project, load_comp(project, "main")).cuts}[layer_id]


def frame_at(c, seconds):
    """The first comp frame at or after `seconds` of comp time."""
    tick = ticks_per_frame(c.comp_rate)
    return -(-round(seconds * TICKS_PER_SECOND) // tick)


def speed_between(c, f0, f1):
    return float((c.source_ticks(f1) - c.source_ticks(f0)) / ((f1 - f0) * ticks_per_frame(c.comp_rate)))


def remap(project_dir, layer_id):
    return json.loads((project_dir / "comps" / "main.json").read_text())["layers"][
        [layer["id"] for layer in json.loads((project_dir / "comps" / "main.json").read_text())["layers"]].index(layer_id)
    ]["time"]["remap"]


def test_a_range_plays_at_its_speed_and_the_rest_keeps_its_own(cut_project):
    before = cut(cut_project, "shot2")
    result = ops.speed(Project.load(cut_project), "main", "shot2", 1.0, start="b:5", end="b:6")
    assert result["changes"][0].startswith("shot2 time remapped")
    assert result["changes"][1] == "shot2 plays at 100 % from b:5 to b:6; 1 later key moved +0.25000 s in source"
    assert remap(cut_project, "shot2") == [{"t": "0s", "v": "0s"}, {"t": "b:1", "v": "0.25s"},
                                           {"t": "b:2", "v": "0.75s"}, {"t": "b:4", "v": "1.25s"}]
    after = cut(cut_project, "shot2")
    b5, b6 = frame_at(after, 2.75), frame_at(after, 3.25)
    assert [after.samples(f) for f in range(after.first, b5)] == [before.samples(f) for f in range(before.first, b5)]
    assert speed_between(after, b5, b6 - 1) == pytest.approx(1.0, abs=1e-4)
    assert speed_between(after, b6, after.end - 1) == pytest.approx(0.5, abs=1e-4)
    shift = [float(after.source_ticks(f) - before.source_ticks(f)) / TICKS_PER_SECOND for f in range(b6, after.end)]
    assert shift == pytest.approx([0.25] * len(shift), abs=1e-4)


def test_a_segment_takes_a_speed_and_a_hold_shows_one_frame(cut_project):
    ops.speed(Project.load(cut_project), "main", "shot2", 1.0, start="b:5", end="b:6")
    result = ops.speed(Project.load(cut_project), "main", "shot2", 0.25, at="b:7")
    assert result["changes"] == ["shot2 plays at 25 % from b:6 to b:8"]
    assert remap(cut_project, "shot2")[-1] == {"t": "b:4", "v": "1s"}
    ops.speed(Project.load(cut_project), "main", "shot1", 0.0, start="b:1", end="b:2")
    shot1 = cut(cut_project, "shot1")
    held = {shot1.source_frame(f) for f in range(frame_at(shot1, 0.75), frame_at(shot1, 1.25))}
    assert len(held) == 1


def test_a_range_outside_the_layer_is_taken_inside_its_in_and_out_points(cut_project):
    result = ops.speed(Project.load(cut_project), "main", "shot1", 0.5, start="0s", end="b:10")
    assert result["changes"][1] == "shot1 plays at 50 % from its in point to its out point"
    with pytest.raises(AssertionError, match="does not overlap shot1"):
        ops.speed(Project.load(cut_project), "main", "shot1", 0.5, start="b:5", end="b:6")


def test_a_key_inside_an_eased_segment_is_refused(cut_project):
    ops.remap(Project.load(cut_project), "main", "shot2")
    ops.key(Project.load(cut_project), "main", "shot2", "remap", "b:8", "1s", ease="in_out")
    with pytest.raises(AssertionError, match="inside the eased remap segment"):
        ops.speed(Project.load(cut_project), "main", "shot2", 1.0, start="b:5", end="b:6")


def test_a_speed_that_runs_out_of_footage_is_refused(cut_project):
    with pytest.raises(AssertionError, match="needs source frame"):
        ops.speed(Project.load(cut_project), "main", "shot2", 3.0, start="b:5", end="b:7")
    assert "remap" not in json.dumps(json.loads((cut_project / "comps" / "main.json").read_text()))


def test_op_speed_from_the_command_line(cut_project):
    result = CliRunner().invoke(main, ["op", "speed", "main", "shot2", "--speed", "1", "--from", "b:5", "--to", "b:6",
                                       "--project", str(cut_project)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changes"][1].startswith("shot2 plays at 100 % from b:5 to b:6")
