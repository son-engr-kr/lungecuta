"""lungecuta framing: the comp's window in a footage layer's frame (src/lungecuta/framing.py)."""
import json

import pytest
from click.testing import CliRunner
from PIL import Image

from clips import comp_json, put_comp
from lungecuta.cli import main


def framing(project, *args, **layer_changes):
    comp = comp_json(**({"size": layer_changes.pop("size")} if "size" in layer_changes else {}))
    comp["layers"][1] |= layer_changes
    put_comp(project, comp)
    result = CliRunner().invoke(main, ["framing", "main", "shot1", *args, "--project", str(project)],
                                catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def corners(entry):
    return [tuple(round(v, 4) for v in corner) for corner in entry["corners"]]


def test_an_untransformed_layer_is_the_comp_s_frame(cut_project):
    printed = framing(cut_project)
    first, last = printed["windows"]
    assert (first["which"], first["frame"], last["which"], last["frame"]) == ("first", 8, "last", 67)
    assert corners(first) == [(0, 0), (1, 0), (1, 1), (0, 1)] and first["covered"]
    assert printed["uncovered"] == [] and Image.open(printed["png"]).size == (128, 72)


def test_a_push_in_is_covered_and_a_pull_back_is_not(cut_project):
    push = framing(cut_project, transform={"scale": 2.0})
    assert corners(push["windows"][0]) == [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]
    assert push["uncovered"] == []
    pull = framing(cut_project, transform={"scale": 0.5})
    assert corners(pull["windows"][0])[0] == (-0.5, -0.5)
    assert pull["uncovered"] == [{"from_frame": 8, "to_frame": 67}]


def test_a_vertical_cut_from_wide_footage_sits_in_the_middle(cut_project):
    printed = framing(cut_project, size=[36, 64], transform={"scale": 64 / 72})
    (left, top), _, (right, bottom), _ = corners(printed["windows"][0])
    assert left == pytest.approx(0.5 - 36 / 2 / 128 / (64 / 72), abs=1e-4) and right == pytest.approx(1 - left, abs=1e-4)
    assert (top, bottom) == (0, 1) and printed["uncovered"] == []


def test_a_pan_that_runs_past_the_edge_is_found(cut_project):
    keys = {"keys": [{"t": "b:0", "v": [0.5, 0.5]}, {"t": "b:4", "v": [0.75, 0.5]}]}
    printed = framing(cut_project, "--at", "b:2", transform={"scale": 1.25, "position": keys})
    first, last, middle = printed["windows"]
    assert first["covered"] and not last["covered"] and middle["which"] == "at"
    (run,) = printed["uncovered"]
    assert 8 < run["from_frame"] < 67 and run["to_frame"] == 67  # from where x passes 0.625 to the end
    turned = framing(cut_project, transform={"rotation": 90})
    assert turned["uncovered"] == [{"from_frame": 8, "to_frame": 67}]  # a quarter turn of 16:9 in 16:9
