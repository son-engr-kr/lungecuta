import json

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from clips import comp_json, put_comp
from lungecuta.cli import main
from lungecuta.comp import load_comp, resolve
from lungecuta.media import Project
from lungecuta.schema.style import StyleFile
from lungecuta.ticks import TICKS_PER_SECOND, ticks_per_frame

# A slow-down on a beat: 100 % one beat before, 50 % on it, 100 % one beat after, linear
# between, and a punch-in that settles over half a beat.
DIP = {
    "id": "dip", "name": "slow motion on the beat", "anchor": "beat",
    "params": {"intensity": {"default": 1.0, "min": 0, "max": 2}},
    "tracks": {
        "speed": [{"at": "b:-1", "v": 1.0}, {"at": "b:0", "v": 0.5}, {"at": "b:1", "v": 1.0}],
        "scale": [{"at": "b:0", "v": 1.5, "ease": "out"}, {"at": "b:0.5", "v": 1.0}],
    },
}
BEAT = TICKS_PER_SECOND // 2  # the test music is at 120 BPM


def write_style(project, style=DIP):
    (project / "styles" / f"{style['id']}.json").write_text(json.dumps(style))


def styled(project, **instance):
    comp = comp_json()
    comp["layers"][1]["time"] = {"style": "dip", "at": "b:2", **instance}
    put_comp(project, comp)
    loaded = Project.load(project)
    return {cut.layer.id: cut for cut in resolve(loaded, load_comp(loaded, "main")).cuts}["shot1"]


def at_100(cut, frame):
    return cut.source_start + (frame * ticks_per_frame(cut.comp_rate) - cut.start)


def test_a_style_file_is_checked():
    StyleFile.model_validate(DIP)
    StyleFile.model_validate(DIP | {"tracks": {"shutter_deg": [{"at": "b:0", "v": 180}]}})  # motion blur is written
    with pytest.raises(ValidationError):
        StyleFile.model_validate(DIP | {"tracks": {"shutter_deg": [{"at": "b:0", "v": 800}]}})
    with pytest.raises(ValidationError):
        StyleFile.model_validate(DIP | {"tracks": {"opacity": [{"at": "b:0", "v": 1.5}]}})
    with pytest.raises(ValidationError):
        StyleFile.model_validate(DIP | {"tracks": {"speed": [{"at": "b:0", "v": -1}]}})
    with pytest.raises(ValidationError):
        StyleFile.model_validate(DIP | {"params": {"intensity": {"default": 3, "min": 0, "max": 2}}})


def test_a_speed_track_integrates_to_source_time(cut_project):
    write_style(cut_project)
    cut = styled(cut_project)
    # before the track (comp beat 1, 0.75 s) the layer plays at 100 %
    assert cut.source_ticks(10) == at_100(cut, 10)
    # after it (comp beat 3, 1.75 s) the dip has cost half of two beats at an average of 75 %
    behind = BEAT // 2  # 1 - 0.75 over two beats
    assert abs(cut.source_ticks(60) - (at_100(cut, 60) - behind)) <= 1
    # on the anchor (1.25 s), half of the cost is behind: one beat at an average of 75 %
    frame = 40  # 1.335 s, inside the second half
    t = frame * ticks_per_frame(cut.comp_rate)
    anchor = round(1.25 * TICKS_PER_SECOND)
    u = (t - anchor) / BEAT
    lost = BEAT / 4 + BEAT * (0.5 * u - 0.25 * u * u)  # integral of 1 - speed
    assert abs(cut.source_ticks(frame) - (at_100(cut, frame) - lost)) <= 1


def test_intensity_moves_values_from_neutral(cut_project):
    write_style(cut_project)
    assert styled(cut_project, overrides={"intensity": 0.0}).source_ticks(60) == styled(cut_project).source_start + (
        60 * ticks_per_frame(styled(cut_project).comp_rate) - styled(cut_project).start)
    half = styled(cut_project, overrides={"intensity": 0.5})
    assert abs(half.source_ticks(60) - (at_100(half, 60) - BEAT // 4)) <= 1
    with pytest.raises(AssertionError, match="outside 0.0 to 2.0"):
        styled(cut_project, overrides={"intensity": 3.0})


def test_a_scale_track_multiplies_the_layer_scale_and_is_neutral_outside(cut_project):
    write_style(cut_project)
    cut = styled(cut_project)
    tick = ticks_per_frame(cut.comp_rate)
    anchor = round(1.25 * TICKS_PER_SECOND)
    assert cut.placement.at(anchor)[1] == (1.5, 1.5)  # the punch-in lands on the beat
    assert cut.placement.at(anchor - tick)[1] == (1.0, 1.0)  # and not before it
    assert cut.placement.at(anchor + BEAT // 2 + tick)[1] == (1.0, 1.0)


def test_lc_op_style_places_replaces_and_takes_off(cut_project):
    write_style(cut_project)
    runner = CliRunner()
    args = ["--project", str(cut_project)]
    placed = runner.invoke(main, ["op", "style", "main", "shot1", "dip", "--at", "b:2", *args])
    assert placed.exit_code == 0, placed.output
    layer = json.loads((cut_project / "comps" / "main.json").read_text())["layers"][1]
    assert layer["time"] == {"style": "dip", "at": "b:2"}
    again = runner.invoke(main, ["op", "style", "main", "shot1", "dip", "--at", "b:3", "--intensity", "0.5", *args])
    assert again.exit_code == 0 and "replaced" in again.output
    layer = json.loads((cut_project / "comps" / "main.json").read_text())["layers"][1]
    assert layer["time"] == {"style": "dip", "at": "b:3", "overrides": {"intensity": 0.5}}
    # shot2 plays at a constant speed of its own, which a style does not replace
    refused = runner.invoke(main, ["op", "style", "main", "shot2", "dip", "--at", "b:6", *args])
    assert refused.exit_code != 0 and "has its own time" in str(refused.exception)
    off = runner.invoke(main, ["op", "unstyle", "main", "shot1", *args])
    assert off.exit_code == 0
    assert "time" not in json.loads((cut_project / "comps" / "main.json").read_text())["layers"][1]


def test_editing_a_style_changes_its_placements_and_the_preview_sees_it(cut_project):
    from lungecuta.preview import Preview

    write_style(cut_project)
    styled(cut_project)
    preview = Preview(Project.load(cut_project), "main")
    assert not preview.stale()
    before = preview.version
    write_style(cut_project, DIP | {"tracks": {"speed": [{"at": "b:-1", "v": 1.0}, {"at": "b:1", "v": 1.0}]}})
    assert preview.stale()
    preview.reload()
    assert preview.version != before and preview.hash == preview.hash


def test_fcpxml_writes_a_styled_cut_as_a_point_per_frame(cut_project):
    import xml.etree.ElementTree as ET

    style = DIP | {"tracks": {"speed": DIP["tracks"]["speed"]}}  # no scale: FCPXML refuses transforms
    write_style(cut_project, style)
    cut = styled(cut_project)
    result = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    root = ET.parse(json.loads(result.output)["fcpxml"]).getroot()
    clip = next(c for c in root.iter("asset-clip") if c.get("name") == "shot1")
    points = clip.find("timeMap").findall("timept")
    assert len(points) == cut.end - cut.first + 1


def test_a_styled_comp_renders_and_verifies(cut_project):
    write_style(cut_project)
    cut = styled(cut_project)
    assert cut.source_frame(60) < cut.source_frame(10) + 50  # behind where 100 % would be
    args = ["--project", str(cut_project)]
    rendered = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", *args])
    assert rendered.exit_code == 0, rendered.output
    checked = CliRunner().invoke(main, ["verify", json.loads(rendered.output)["file"], "--comp", "main",
                                        "--preset", "master-prores-hq", *args])
    assert checked.exit_code == 0, checked.output


WHIP = {
    "id": "whip", "name": "a whip: a turn and a dip in opacity, the shutter open through it", "anchor": "beat",
    "params": {"intensity": {"default": 1.0, "min": 0, "max": 2}},
    "tracks": {
        "rotation": [{"at": "b:-0.5", "v": 0}, {"at": "b:0", "v": 20}, {"at": "b:0.5", "v": 0}],
        "opacity": [{"at": "b:-0.5", "v": 1}, {"at": "b:0", "v": 0.5}, {"at": "b:0.5", "v": 1}],
        "shutter_deg": [{"at": "b:-0.5", "v": 180}, {"at": "b:0.5", "v": 180}],
    },
}


def whipped(project, intensity=None, **layer):
    write_style(project, WHIP)
    comp = comp_json()
    comp["layers"][1] |= {"time": {"style": "whip", "at": "b:2",
                                   **({"overrides": {"intensity": intensity}} if intensity is not None else {})},
                          **layer}
    put_comp(project, comp)
    loaded = Project.load(project)
    return {cut.layer.id: cut for cut in resolve(loaded, load_comp(loaded, "main")).cuts}["shot1"]


def test_rotation_opacity_and_shutter_tracks_move_from_their_neutrals(cut_project):
    cut = whipped(cut_project)
    anchor = cut.start + 2 * BEAT  # b:2 from the layer's start
    assert cut.placement.rotation_at(anchor) == pytest.approx(20)
    assert cut.placement.at(anchor)[2] == pytest.approx(0.5)
    assert cut.placement.rotation_at(anchor + BEAT) == 0 and cut.placement.at(anchor + BEAT)[2] == 1  # outside
    assert cut.shutter_deg(anchor) == 180 and cut.shutter_deg(anchor + BEAT) == 0
    half = whipped(cut_project, intensity=0.5)
    assert half.placement.rotation_at(anchor) == pytest.approx(10)  # 0 + 0.5 (20 - 0)
    assert half.placement.at(anchor)[2] == pytest.approx(0.75)  # 1 + 0.5 (0.5 - 1)
    assert half.shutter_deg(anchor) == pytest.approx(90)


def test_a_style_s_shutter_holds_inside_its_keys_and_the_layer_s_outside(cut_project):
    cut = whipped(cut_project, motion_blur={"shutter_deg": 45})
    anchor = cut.start + 2 * BEAT
    assert cut.shutter_deg(anchor) == 180 and cut.shutter_deg(anchor + BEAT) == 45



def test_a_style_on_a_layer_that_starts_before_the_grid_is_anchored_in_seconds(cut_project):
    from lungecuta import ops

    write_style(cut_project)
    comp = comp_json()
    comp["layers"][1] |= {"start": "0s", "in": "0.25s", "out": "2.25s", "markers": []}  # starts before the grid
    put_comp(cut_project, comp)
    ops.style(Project.load(cut_project), "main", "shot1", "dip", "b:2")
    written = json.loads((cut_project / "comps" / "main.json").read_text())["layers"][1]["time"]
    assert written["at"] == "1.25s"  # b:2 is at 1.25 s, a beat span from 0 s does not exist
    project = Project.load(cut_project)
    shot1 = {c.layer.id: c for c in resolve(project, load_comp(project, "main")).cuts}["shot1"]
    assert shot1.speed_track.at(1 * TICKS_PER_SECOND + TICKS_PER_SECOND // 4)[0] == pytest.approx(0.5)  # on b:2
