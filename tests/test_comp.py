"""A comp resolved to frames: the beat grid from music layers, cuts on the first frame at or
after their time, and the source frame each comp frame shows."""
import json

import pytest

from lungecuta.comp import literals_past, load_comp, resolve
from lungecuta.media import Project
from lungecuta.ticks import TICKS_PER_SECOND

from clips import comp_json, make_clip, put_comp, write_record


def resolved(project, name="main"):
    loaded = Project.load(project)
    return resolve(loaded, load_comp(loaded, name))


def test_the_grid_comes_from_the_music_layer(cut_project):
    result = resolved(cut_project)
    assert result.grid.times[0] == TICKS_PER_SECOND // 4
    assert result.grid.times[4] == round(2.25 * TICKS_PER_SECOND)
    assert result.grid.downbeats == (0, 4, 8, 12, 16)
    # b:12 is 6.25 s, 187.31 frames: the comp ends on frame 188.
    assert result.end == 188


def test_cuts_land_on_the_first_frame_at_or_after_their_beat(cut_project):
    shot1, shot2 = resolved(cut_project).cuts
    # b:0 is 0.25 s, 7.49 frames at 29.97; b:4 is 2.25 s, 67.43 frames; b:8 is 4.25 s, 127.37.
    assert (shot1.first, shot1.end) == (8, 68)
    assert (shot2.first, shot2.end) == (68, 128)


def test_a_source_time_between_frames_shows_the_frame_it_falls_in(cut_project):
    shot1, shot2 = resolved(cut_project).cuts
    # shot1 plays from source frame 2 at its start, half a frame before comp frame 8.
    assert shot1.source_frames == (2, 61)
    # shot2 at half speed from 0s: comp frame 68 is 0.28 frames into the layer.
    assert shot2.source_frames == (0, 29)
    assert shot2.source_frame(70) == 1


def test_markers_land_on_the_first_frame_at_or_after_their_time(cut_project):
    markers = {at.marker.name: at for at in resolved(cut_project).markers}
    assert (markers["drop"].frame, markers["drop"].layer) == (68, None)
    # b:1 from shot1's start is comp beat 1, 0.75 s, 22.48 frames.
    assert (markers["hit"].frame, markers["hit"].layer) == (23, "shot1")


def test_a_placement_needs_enough_source(cut_project):
    comp = comp_json()
    comp["layers"][2]["time"] = {"speed": 2.0}
    put_comp(cut_project, comp)
    with pytest.raises(AssertionError, match="needs source frame 119 of C0001, which has 70 frames"):
        resolved(cut_project)


def test_overlapping_footage_resolves_but_the_fcpxml_writes_one_track(cut_project):
    comp = comp_json()
    comp["layers"][2]["start"] = "b:3"
    put_comp(cut_project, comp)
    result = resolved(cut_project)
    assert [cut.layer.id for cut in result.stack(60)] == ["shot1", "shot2"]  # bottom first, as listed
    with pytest.raises(AssertionError, match="writes one picture track"):
        result.one_track()


def test_the_grid_layer_cannot_be_placed_on_beats(cut_project):
    comp = comp_json()
    comp["layers"][0]["start"] = "b:0"
    put_comp(cut_project, comp)
    with pytest.raises(AssertionError, match="cannot be a beat literal"):
        resolved(cut_project)


def test_a_source_start_before_the_media_is_refused(cut_project):
    comp = comp_json()
    comp["layers"][1]["source_start"] = "-1s"
    put_comp(cut_project, comp)
    with pytest.raises(AssertionError, match="cannot be negative"):
        resolved(cut_project)


def test_unsupported_fields_are_errors_not_ignored(cut_project):
    comp = comp_json()
    comp["layers"][1]["retime"] = "flow"
    put_comp(cut_project, comp)
    with pytest.raises(Exception, match="retime"):
        resolved(cut_project)


def test_an_undecided_clip_cannot_be_placed(cut_project):
    write_record(cut_project, make_clip(cut_project / "footage" / "C0002.mov", frames=70), decided=False)
    comp = comp_json()
    comp["layers"][1]["media"] = "C0002"
    put_comp(cut_project, comp)
    with pytest.raises(AssertionError, match="undecided in colour"):
        resolved(cut_project)


def test_a_comp_must_be_listed(cut_project):
    (cut_project / "comps" / "other.json").write_text(json.dumps(comp_json(id="other")))
    with pytest.raises(AssertionError, match="not one of the comps"):
        resolved(cut_project, "other")


def test_a_refused_reanalysis_lists_the_literals_it_would_move(cut_project):
    project = Project.load(cut_project)
    affected = literals_past(project, "track", 4)
    assert [(a["where"], a["literal"]) for a in affected] == [
        ("end", "b:12"), ("marker drop", "b:4"), ("shot1.out", "b:4"), ("shot2.start", "b:4"), ("shot2.out", "b:4"),
    ]
    assert literals_past(project, "track", 20) == []


def test_the_first_changed_beat_is_where_the_numbering_moves():
    from lungecuta.beats import first_changed
    from lungecuta.music import match

    from clips import beat_map

    current = beat_map([0.5, 1.0, 1.5, 2.0, 2.5])
    assert first_changed(match(current, beat_map([0.5, 1.0, 1.25, 1.5, 2.0, 2.5]))) == 2
    assert first_changed(match(current, beat_map([0.5, 1.0, 2.0, 2.5]))) == 2
    assert first_changed(match(current, beat_map([0.5, 1.0, 1.5, 2.0, 2.5], downbeats=(3,)))) == 0
    assert first_changed(match(current, beat_map([0.51, 1.0, 1.5, 2.0, 2.5]))) == 5


def test_lc_validate_resolves_every_comp(cut_project):
    from click.testing import CliRunner

    from lungecuta.cli import main

    write_record(cut_project, make_clip(cut_project / "footage" / "C0002.mov", frames=3), decided=False)
    result = CliRunner().invoke(main, ["validate", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "valid": True, "media": 3, "undecided": ["C0002"],
        "comps": [{"comp": "main", "frames": 188, "cuts": 2, "sounds": 1, "beats": 20}],
    }
    (cut_project / "footage" / "C0001.mov").write_bytes(b"changed")
    result = CliRunner().invoke(main, ["validate", "--project", str(cut_project)])
    assert result.exit_code != 0 and "has changed since it was imported" in str(result.exception)


def remapped(cut_project, keys, retime=None):
    comp = comp_json()
    shot1 = comp["layers"][1]
    del shot1["source_start"]
    shot1["time"] = {"remap": keys}
    if retime:
        comp["layers"][2]["retime"] = retime
    put_comp(cut_project, comp)
    return resolved(cut_project)


def test_a_remap_eases_through_its_keys(cut_project):
    result = remapped(cut_project, [{"t": "b:0", "v": "f:2"}, {"t": "b:4", "v": "f:60", "ease": "in_out"}])
    shot1 = result.cuts[0]
    # In and out of an in_out ease the source creeps; in the middle it is halfway.
    assert shot1.source_frame(8) == 2 and shot1.source_frames == (2, 59)
    middle = (8 + 68) // 2
    assert abs(shot1.source_frame(middle) - 31) <= 1
    assert shot1.forwards


def test_a_remap_can_play_backwards(cut_project):
    result = remapped(cut_project, [{"t": "b:0", "v": "f:60"}, {"t": "b:4", "v": "f:0"}])
    shot1 = result.cuts[0]
    assert not shot1.forwards and shot1.source_frame(8) > shot1.source_frame(60)


def test_a_layer_has_a_source_start_or_remap_keys_not_both(cut_project):
    comp = comp_json()
    comp["layers"][1]["time"] = {"remap": [{"t": "0s", "v": "f:0"}, {"t": "1s", "v": "f:30"}]}
    put_comp(cut_project, comp)
    with pytest.raises(Exception, match="a source_start or remap keys"):
        resolved(cut_project)


def test_retime_picks_nearest_or_blends_between_frames(cut_project):
    blend = remapped(cut_project, [{"t": "b:0", "v": "f:2"}, {"t": "b:4", "v": "f:60"}], retime="blend").cuts[1]
    # shot2 at half speed: comp frame 70 is about 1.03 source frames in, between 1 and 2.
    weights = blend.samples(70)
    assert [index for index, _ in weights] == [1, 2] and sum(w for _, w in weights) == pytest.approx(1)
    nearest = remapped(cut_project, [{"t": "b:0", "v": "f:2"}, {"t": "b:4", "v": "f:60"}], retime="nearest").cuts[1]
    assert nearest.samples(70) == [(round(1.0 + weights[1][1]), 1.0)]
