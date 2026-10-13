"""Edit operations on the comp in tests/clips.py: shot1 from b:0 for 4 beats, shot2 from b:4
for 4 beats at half speed, music under them, 120 BPM with the first beat at 0.25 s."""
import json

import pytest
from click.testing import CliRunner

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.media import Project

from clips import comp_json, put_comp


def layers(project):
    return {layer["id"]: layer for layer in json.loads((project / "comps" / "main.json").read_text())["layers"]}


def test_a_move_keeps_beat_literals_on_beats(cut_project):
    result = ops.move(Project.load(cut_project), "main", "shot2", "b:1")
    assert layers(cut_project)["shot2"]["start"] == "b:5"
    assert result["changes"] == ["shot2 start moved b:4 to b:5"]
    ops.move(Project.load(cut_project), "main", "shot2", "b:-0.5")
    assert layers(cut_project)["shot2"]["start"] == "b:4+0.5"


def test_a_trim_moves_one_point_and_leaves_the_start(cut_project):
    ops.trim(Project.load(cut_project), "main", "shot1", "out", "b:-0.5")
    shot1 = layers(cut_project)["shot1"]
    assert (shot1["start"], shot1["out"]) == ("b:0", "b:3.5")
    with pytest.raises(AssertionError, match="puts in after out"):
        ops.trim(Project.load(cut_project), "main", "shot1", "in", "b:4")


def test_a_slip_changes_the_source_in_its_own_unit(cut_project):
    ops.slip(Project.load(cut_project), "main", "shot1", "f:3")
    assert layers(cut_project)["shot1"]["source_start"] == "f:5"
    ops.slip(Project.load(cut_project), "main", "shot1", "f:-5")
    assert layers(cut_project)["shot1"]["source_start"] == "f:0"
    with pytest.raises(AssertionError, match="cannot be negative"):
        ops.slip(Project.load(cut_project), "main", "shot1", "f:-1")


def test_a_roll_moves_the_cut_between_two_layers(cut_project):
    ops.roll(Project.load(cut_project), "main", "shot1", "shot2", "b:0.5")
    found = layers(cut_project)
    assert found["shot1"]["out"] == "b:4.5" and found["shot2"]["in"] == "b:0.5"
    assert found["shot2"]["start"] == "b:4", "the start, and so the keys, stay"


def test_a_ripple_delete_closes_the_gap_and_leaves_the_music(cut_project):
    ops.ripple_delete(Project.load(cut_project), "main", "shot1")
    found = layers(cut_project)
    assert "shot1" not in found and found["shot2"]["start"] == "b:0" and found["music"]["start"] == "0s"


def test_a_split_makes_two_layers_with_the_same_start_and_source(cut_project):
    ops.split(Project.load(cut_project), "main", "shot1", "b:2")
    found = layers(cut_project)
    assert found["shot1"]["out"] == "b:2"
    # shot1 had no `in`, so the new one takes the unit of the place given, beats.
    assert (found["shot1.2"]["start"], found["shot1.2"]["in"], found["shot1.2"]["out"]) == ("b:0", "b:2", "b:4")
    assert found["shot1.2"]["source_start"] == found["shot1"]["source_start"]


def test_snapping_puts_the_points_on_beats(cut_project):
    comp = comp_json()
    comp["layers"][1]["out"] = "1.6s"  # 0.25 s + 1.6 s = 1.85 s, nearest beat 1.75 s (b:3)
    put_comp(cut_project, comp)
    ops.snap(Project.load(cut_project), "main", "shot1")
    shot1 = layers(cut_project)["shot1"]
    assert (shot1["in"], shot1["out"]) == ("b:0", "b:3")


def test_an_edit_does_not_overwrite_a_change_made_meanwhile(cut_project):
    edit = ops.Edit.open(Project.load(cut_project), "main")
    edit.set_start(edit.layer("shot2"), edit.start(edit.layer("shot2")))
    put_comp(cut_project, comp_json(end="b:13"))  # the person, or another agent, wrote the comp
    with pytest.raises(AssertionError, match="changed while lc was working on it"):
        edit.save()


def test_lc_op_from_the_command_line(cut_project):
    result = CliRunner().invoke(main, ["op", "move", "main", "shot2", "--by", "b:1", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["changes"] == ["shot2 start moved b:4 to b:5"]
    result = CliRunner().invoke(main, ["op", "trim", "main", "shot2", "--out", "b:-1", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    assert layers(cut_project)["shot2"]["out"] == "b:3"


def test_placing_media_adds_a_layer_as_long_as_the_media(cut_project):
    ops.place(Project.load(cut_project), "main", "C0001", "b:1")
    found = layers(cut_project)
    assert found["C0001"] == {"id": "C0001", "type": "footage", "media": "C0001", "start": "b:1",
                              "source_start": "f:0", "out": "f:70"}
    # Near the end it stops at the comp's end (b:12, frame 187.31; b:9 is frame 142.86).
    ops.place(Project.load(cut_project), "main", "C0001", "b:9")
    assert layers(cut_project)["C0001.2"]["out"] == "f:44"
    from lungecuta.comp import load_comp, resolve
    project = Project.load(cut_project)
    resolve(project, load_comp(project, "main"))
    ops.place(Project.load(cut_project), "main", "track", "0s")
    assert layers(cut_project)["track"]["type"] == "audio"


def test_a_new_comp_is_written_and_listed(cut_project):
    result = CliRunner().invoke(main, ["new-comp", "promo", "--size", "1080x1920", "--rate", "30000/1001",
                                       "--end", "8s", "--music", "track", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    settings = json.loads((cut_project / "project.json").read_text())
    assert "comps/promo.json" in settings["comps"]
    comp = json.loads((cut_project / "comps" / "promo.json").read_text())
    assert comp["beats"] == {"from_layers": ["music"]} and comp["layers"][0]["out"] == "f:239"
    from lungecuta.comp import load_comp, resolve
    project = Project.load(cut_project)
    assert resolve(project, load_comp(project, "promo")).grid is not None
    again = CliRunner().invoke(main, ["new-comp", "promo", "--size", "64x36", "--rate", "25", "--end", "1s",
                                      "--project", str(cut_project)])
    assert again.exit_code != 0


def test_keys_are_added_replaced_and_deleted_in_layer_time(cut_project):
    project = Project.load(cut_project)
    result = ops.key(project, "main", "shot1", "scale", "b:1", 1.2, "out")
    assert result["changes"] == ["shot1 scale key added at b:1: 1.2, ease out"]
    ops.key(Project.load(cut_project), "main", "shot1", "scale", "b:3", 1.0)
    ops.key(Project.load(cut_project), "main", "shot1", "scale", "b:1", 1.3)
    assert layers(cut_project)["shot1"]["transform"]["scale"] == {"keys": [{"t": "b:1", "v": 1.3}, {"t": "b:3", "v": 1.0}]}
    # a frame on a layer that starts on a beat (2.25 s, between frames) is written in beats
    ops.key(Project.load(cut_project), "main", "shot2", "opacity", "f:100", 0.5)
    written = layers(cut_project)["shot2"]["transform"]["opacity"]["keys"][0]["t"]
    assert written.startswith("b:")
    from lungecuta.comp import load_comp, resolve
    project = Project.load(cut_project)
    shot2 = {c.layer.id: c for c in resolve(project, load_comp(project, "main")).cuts}["shot2"]
    assert shot2.placement.opacity.times[0] == 100 * 1001 * 705600000 // 30000
    ops.unkey(Project.load(cut_project), "main", "shot1", "scale", "b:1")
    result = ops.unkey(Project.load(cut_project), "main", "shot1", "scale", "b:3")
    assert layers(cut_project)["shot1"]["transform"]["scale"] == 1.0
    assert result["changes"] == ["shot1 scale key at b:3 deleted; scale stays 1.0"]
    with pytest.raises(AssertionError, match="before shot2's start"):
        ops.key(Project.load(cut_project), "main", "shot2", "scale", "b:2", 1.1)
    with pytest.raises(AssertionError, match="no scale key"):
        ops.unkey(Project.load(cut_project), "main", "shot1", "scale", "b:2")


def test_enabling_remap_keeps_every_frame(cut_project):
    from lungecuta.comp import load_comp, resolve

    project = Project.load(cut_project)
    before = {c.layer.id: [c.samples(f) for f in range(c.first, c.end)] for c in resolve(project, load_comp(project, "main")).cuts}
    for layer in ("shot1", "shot2"):  # 100 % and half speed
        ops.remap(Project.load(cut_project), "main", layer)
    project = Project.load(cut_project)
    after = {c.layer.id: [c.samples(f) for f in range(c.first, c.end)] for c in resolve(project, load_comp(project, "main")).cuts}
    assert after == before
    shot2 = layers(cut_project)["shot2"]
    assert "source_start" not in shot2 and [k["t"] for k in shot2["time"]["remap"]] == ["0s", "b:4"]
    # now its source time has keys: a freeze from the middle on
    middle = ops.key(Project.load(cut_project), "main", "shot2", "remap", "b:6", "1s")
    assert "remap key added at b:2" in middle["changes"][0]
    ops.key(Project.load(cut_project), "main", "shot2", "remap", "b:8", "1s")
    with pytest.raises(AssertionError, match="only a layer at 100 % or a constant speed"):
        ops.remap(Project.load(cut_project), "main", "shot2")


def test_a_grade_is_replaced_whole_and_checked(cut_project):
    result = ops.set_grade(Project.load(cut_project), "main", "shot1", [{"op": "primary", "contrast": 1.2}])
    assert result["changes"] == ["shot1 grade none to primary (contrast 1.2)"]
    assert layers(cut_project)["shot1"]["grade"] == [{"op": "primary", "contrast": 1.2}]
    with pytest.raises(AssertionError, match="luts/none.cube is missing"):
        ops.set_grade(Project.load(cut_project), "main", "shot1",
                      [{"op": "lut", "path": "luts/none.cube", "space": "ACEScct", "out": "ACEScct"}])
    ops.set_grade(Project.load(cut_project), "main", "shot1", [])
    assert "grade" not in layers(cut_project)["shot1"]


def test_lc_op_key_parses_pairs_and_source_times(cut_project):
    args = ["--project", str(cut_project)]
    runner = CliRunner()
    assert runner.invoke(main, ["op", "key", "main", "shot1", "position", "--at", "b:1", "--value", "0.4,0.6", *args]).exit_code == 0
    assert layers(cut_project)["shot1"]["transform"]["position"] == {"keys": [{"t": "b:1", "v": [0.4, 0.6]}]}
    assert runner.invoke(main, ["op", "remap", "main", "shot1", *args]).exit_code == 0
    done = runner.invoke(main, ["op", "key", "main", "shot1", "remap", "--at", "b:2", "--value", "f:30", "--ease", "in_out", *args])
    assert done.exit_code == 0, done.output
    grade = runner.invoke(main, ["op", "grade", "main", "shot1", "--ops", '[{"op": "cdl", "slope": [1.1, 1, 1]}]', *args])
    assert grade.exit_code == 0, grade.output


def test_audio_gain_takes_keys_and_fades_take_spans(cut_project):
    args = ["--project", str(cut_project)]
    runner = CliRunner()
    assert runner.invoke(main, ["op", "set", "main", "music", "gain", "--value", "-3", *args]).exit_code == 0
    assert layers(cut_project)["music"]["gain"] == -3.0
    assert runner.invoke(main, ["op", "set", "main", "music", "fade_out", "--value", "1s", *args]).exit_code == 0
    assert layers(cut_project)["music"]["fade_out"] == "1s"
    assert runner.invoke(main, ["op", "set", "main", "music", "fade_out", "--value", "none", *args]).exit_code == 0
    assert "fade_out" not in layers(cut_project)["music"]
    keyed = runner.invoke(main, ["op", "key", "main", "music", "gain", "--at", "5s", "--value", "-12", *args])
    assert keyed.exit_code == 0, keyed.output
    assert layers(cut_project)["music"]["gain"] == {"keys": [{"t": "5s", "v": -12.0}]}
    refused = runner.invoke(main, ["op", "key", "main", "shot1", "gain", "--at", "b:1", "--value", "-3", *args])
    assert "gain is a property of audio layers" in str(refused.exception)


def test_a_rotation_key_turns_the_rendered_layer(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.render import frame_at

    project = Project.load(cut_project)
    still = frame_at(project, resolve(project, load_comp(project, "main")), 30)
    ops.key(project, "main", "shot1", "rotation", "b:1", 90.0)
    project = Project.load(cut_project)
    turned = frame_at(project, resolve(project, load_comp(project, "main")), 30)
    assert layers(cut_project)["shot1"]["transform"]["rotation"] == {"keys": [{"t": "b:1", "v": 90.0}]}
    # a 128x72 frame turned a quarter is 72 wide: the comp's left and right edges go black
    assert turned[:, :20].max() < 1e-6 and still[:, :20].max() > 0.01


def test_a_slide_moves_the_layer_and_its_neighbours_cuts(cut_project):
    from lungecuta.comp import load_comp, resolve

    comp = comp_json()
    comp["layers"][2]["source_start"] = "f:10"  # shot2 has room to start earlier in its source
    # shot3 starts a beat before it is seen, so its in point can move earlier
    comp["layers"].append({"id": "shot3", "type": "footage", "media": "C0001", "start": "b:7", "in": "b:1",
                           "out": "b:3", "source_start": "f:0"})
    put_comp(cut_project, comp)
    result = ops.slide(Project.load(cut_project), "main", "shot2", "b:-1")
    after = layers(cut_project)
    assert after["shot2"]["start"] == "b:3"  # its content comes along
    assert after["shot1"]["out"] == "b:3"  # shot1 ends a beat earlier
    assert (after["shot3"]["start"], after["shot3"]["in"]) == ("b:7", "b:0")  # shot3 is seen a beat earlier
    project = Project.load(cut_project)
    cuts = {c.layer.id: c for c in resolve(project, load_comp(project, "main")).cuts}
    assert cuts["shot1"].end == cuts["shot2"].first and cuts["shot2"].end == cuts["shot3"].first
    assert len(result["changes"]) == 3
    with pytest.raises(AssertionError, match="would move before its start"):
        ops.slide(Project.load(cut_project), "main", "shot2", "b:-1")
