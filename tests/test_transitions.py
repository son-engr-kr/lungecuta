"""Transitions on the comp of tests/clips.py: shot1 (b:0 to b:4) cuts to shot2 (b:4 to b:8,
half speed) on frame 68; at 120 BPM from 0.25 s a beat is 0.5 s."""
import json

import numpy as np
import pytest
from click.testing import CliRunner

from clips import comp_json, put_comp
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.media import Project


def resolved(project, transitions, shot2_source="f:10"):
    comp = comp_json(transitions=transitions)
    comp["layers"][2]["source_start"] = shot2_source  # handles before shot2's in point
    put_comp(project, comp)
    loaded = Project.load(project)
    return loaded, resolve(loaded, load_comp(loaded, "main"))


def cuts(result):
    return {cut.layer.id: cut for cut in result.cuts}


def test_a_dissolve_extends_both_layers_and_ramps_the_upper_one(cut_project):
    _, plain = resolved(cut_project, [])
    assert cuts(plain)["shot1"].end == cuts(plain)["shot2"].first == 68
    project, result = resolved(cut_project, [{"type": "dissolve", "layers": ["shot1", "shot2"], "length": "b:1"}])
    shot1, shot2 = cuts(result)["shot1"], cuts(result)["shot2"]
    # centred on the cut's frame (68, 2.269 s): half a beat either side, 2.019 s to 2.519 s,
    # lands on frames 61 to 76
    assert (shot2.first, shot1.end) == (61, 76)
    assert shot2.ramps == ((61, 76, 0.0, 1.0),) and shot1.ramps == ()
    assert shot2.opacity_at(61) == 0 and shot2.opacity_at(66) == pytest.approx(1 / 3) and shot2.opacity_at(76) == 1
    from lungecuta.compose import frame_at

    project_plain, plain = resolved(cut_project, [])
    before = {f: frame_at(project_plain, plain, f) for f in (61, 76)}
    project, result = resolved(cut_project, [{"type": "dissolve", "layers": ["shot1", "shot2"], "length": "b:1"}])
    assert np.array_equal(frame_at(project, result, 61), before[61])  # all outgoing
    assert np.array_equal(frame_at(project, result, 76), before[76])  # all incoming
    middle = frame_at(project, result, 67)
    assert not np.array_equal(middle, frame_at(project_plain, plain, 67))


def test_a_dissolve_needs_handles_and_a_cut(cut_project):
    with pytest.raises(AssertionError, match="starts before the first frame"):
        resolved(cut_project, [{"type": "dissolve", "layers": ["shot1", "shot2"], "length": "b:1"}], shot2_source="0s")
    comp = comp_json(transitions=[{"type": "dissolve", "layers": ["shot2", "shot1"], "length": "b:1"}])
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    with pytest.raises(AssertionError, match="sits on a cut"):
        resolve(project, load_comp(project, "main"))


def test_a_dip_and_fades_ramp_through_black(cut_project):
    _, result = resolved(cut_project, [{"type": "dip", "layers": ["shot1", "shot2"], "length": "b:1"},
                                       {"type": "fade_in", "layers": ["shot1"], "length": "0.5s"},
                                       {"type": "fade_out", "layers": ["shot2"], "length": "b:1"}])
    shot1, shot2 = cuts(result)["shot1"], cuts(result)["shot2"]
    assert (shot1.first, shot1.end, shot2.first) == (8, 68, 68)  # a dip extends nothing
    assert (61, 68, 1.0, 0.0) in shot1.ramps and (68, 76, 0.0, 1.0) in shot2.ramps
    assert (8, 23, 0.0, 1.0) in shot1.ramps  # 0.267 s + 0.5 s lands on frame 23
    assert shot2.ramps[-1][1] == shot2.end and shot2.opacity_at(shot2.end - 1) < 0.1


def test_lc_op_transition_adds_replaces_and_takes_off(cut_project):
    comp = comp_json()
    comp["layers"][2]["source_start"] = "f:10"
    put_comp(cut_project, comp)
    args = ["--project", str(cut_project)]
    runner = CliRunner()
    added = runner.invoke(main, ["op", "transition", "main", "dissolve", "shot1", "shot2", "--length", "b:1", *args])
    assert added.exit_code == 0, added.output
    again = runner.invoke(main, ["op", "transition", "main", "dissolve", "shot1", "shot2", "--length", "b:0.5", *args])
    assert "replaced" in again.output
    stored = json.loads((cut_project / "comps" / "main.json").read_text())["transitions"]
    assert stored == [{"type": "dissolve", "layers": ["shot1", "shot2"], "length": "b:0.5"}]
    fcpxml = runner.invoke(main, ["fcpxml", "main", *args])
    assert "not written to FCPXML yet" in str(fcpxml.exception)
    rendered = runner.invoke(main, ["render", "main", "--preset", "master-prores-hq", *args])
    assert rendered.exit_code == 0, rendered.output
    checked = runner.invoke(main, ["verify", json.loads(rendered.output)["file"], "--comp", "main",
                                   "--preset", "master-prores-hq", *args])
    assert checked.exit_code == 0, checked.output
    assert runner.invoke(main, ["op", "untransition", "main", "dissolve", "shot1", "shot2", *args]).exit_code == 0
    assert "transitions" not in json.loads((cut_project / "comps" / "main.json").read_text())
