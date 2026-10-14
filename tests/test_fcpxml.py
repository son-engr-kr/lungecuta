"""FCPXML of the comp in tests/clips.py (`cut_project`): times over one denominator, in
points against the clip's start timecode, speed as a time map, music below the spine."""
import json
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from fractions import Fraction
from pathlib import Path

import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.fcpxml import Clock, file_uri, timecode_frames, timecode_text
from lungecuta.tools import tool

# 240000 is lcm(30000, 48000): a frame at 29.97 is 8008 of these, a 48 kHz sample 5.
FRAME = 8008
TC = 645  # 00:00:21:15 at 29.97, non-drop-frame


def seconds(text: str) -> Fraction:
    assert text.endswith("s")
    return Fraction(text[:-1])


def exported(project: Path) -> tuple[dict, ET.Element]:
    result = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    return printed, ET.parse(printed["fcpxml"]).getroot()


def test_times_share_one_denominator_that_holds_frames_and_samples():
    clock = Clock(240000)
    assert clock.time(0) == "0s"
    assert clock.time(705_600_000) == "1s"
    assert clock.time(705_600_000 * 1001 // 30000) == "8008/240000s"
    with pytest.raises(AssertionError, match="not a whole number"):
        clock.time(1)
    assert clock.exact(1) == "1/705600000s"


def test_timecode_reads_and_writes_non_drop_frame():
    rate = Fraction(30000, 1001)
    assert timecode_frames("00:00:21:15", rate) == TC
    assert timecode_text(TC + 2, rate) == "00:00:21:17"
    with pytest.raises(AssertionError, match="frames run from 00 to 29"):
        timecode_frames("00:00:21:30", rate)


def test_file_urls_are_percent_encoded():
    assert file_uri(Path("/a/b c/영상.mp4")) == "file:///a/b%20c/%EC%98%81%EC%83%81.mp4"


def test_the_spine_holds_the_cuts_with_gaps_and_the_in_points_follow_the_timecode(cut_project):
    printed, root = exported(cut_project)
    assert root.get("version") == "1.10"
    sequence = root.find("library/event/project/sequence")
    assert seconds(sequence.get("duration")) == Fraction(188 * FRAME, 240000)
    spine = list(sequence.find("spine"))
    assert [element.tag for element in spine] == ["gap", "asset-clip", "asset-clip", "gap"]
    gap, shot1, shot2, tail = spine
    assert (gap.get("offset"), gap.get("duration")) == ("0s", f"{8 * FRAME}/240000s")
    # shot1 shows source frame 2 first: the asset's timecode plus two frames.
    assert shot1.get("offset") == f"{8 * FRAME}/240000s"
    assert shot1.get("start") == f"{(TC + 2) * FRAME}/240000s"
    assert shot1.get("duration") == f"{60 * FRAME}/240000s"
    assert shot1.get("srcEnable") == "video"
    asset = root.find(f"resources/asset[@id='{shot1.get('ref')}']")
    assert asset.get("start") == f"{TC * FRAME}/240000s" and asset.get("duration") == f"{70 * FRAME}/240000s"
    assert asset.find("media-rep").get("src") == file_uri(cut_project / "footage" / "C0001.mov")
    assert seconds(tail.get("offset")) == Fraction(128 * FRAME, 240000)
    assert printed["cuts"][0] | {"check": None} == {
        "layer": "shot1", "media": "C0001", "comp_frames": [8, 68], "timeline_tc_in": "00:00:00:08",
        "source_frames": [2, 61], "source_tc_in": "00:00:21:17", "speed": 1.0, "check": None,
    }
    assert printed["cuts"][0]["check"] == "lungecuta still --media C0001 --at f:2"


def test_constant_speed_is_a_linear_time_map(cut_project):
    _, root = exported(cut_project)
    shot2 = root.findall("library/event/project/sequence/spine/asset-clip")[1]
    first, last = shot2.find("timeMap").findall("timept")
    start = seconds(shot2.get("start"))
    assert start == Fraction(TC * FRAME, 240000)  # source frame 0
    assert seconds(first.get("time")) == start and first.get("interp") == "linear"
    duration = seconds(shot2.get("duration"))
    assert seconds(last.get("time")) == start + duration
    # Half speed: the source advances half the clip's duration, from the exact source time
    # at its first frame (0.28 frames in).
    source_at_first = Fraction(1, 2) * (Fraction(68 * 1001, 30000) - Fraction(9, 4))
    assert seconds(first.get("value")) == start + source_at_first
    assert seconds(last.get("value")) - seconds(first.get("value")) == duration / 2


def test_music_is_the_wav_below_the_spine(cut_project):
    printed, root = exported(cut_project)
    gap = root.find("library/event/project/sequence/spine/gap")
    music = gap.find("asset-clip")
    assert (music.get("lane"), music.get("offset"), music.get("start"), music.get("duration")) == ("-1", "0s", "0s", "10s")
    asset = root.find(f"resources/asset[@id='{music.get('ref')}']")
    assert (asset.get("hasVideo"), asset.get("audioRate"), asset.get("duration")) == ("0", "48000", "10s")
    assert asset.find("media-rep").get("src") == file_uri((cut_project / "cache" / "audio" / "track.wav").resolve())
    assert printed["sounds"] == [{"layer": "music", "media": "track", "comp_samples": [0, 480000], "source_sample": 0}]
    assert printed["grid"] == {"beats": 20, "downbeats": 5}


def test_markers_sit_on_the_element_at_their_frame_in_its_local_time(cut_project):
    _, root = exported(cut_project)
    shot1, shot2 = root.findall("library/event/project/sequence/spine/asset-clip")
    drop = shot2.find("marker")
    assert (drop.get("value"), drop.get("note")) == ("drop", "the hit")
    assert drop.get("start") == shot2.get("start") and drop.get("duration") == f"{FRAME}/240000s"
    hit = shot1.find("marker")
    # Frame 23 is 15 frames into shot1, whose local time starts at its in point.
    assert hit.get("start") == f"{(TC + 2 + 15) * FRAME}/240000s"


@pytest.mark.network
def test_the_document_is_valid_against_apples_fcpxml_1_10_dtd(cut_project, tmp_path):
    """Apple publishes the DTD on its FCPXML reference page; it is fetched, not committed."""
    url = ("https://developer.apple.com/tutorials/data/documentation/professional-video-applications/"
           "document-type-definition.json")
    page = json.load(urllib.request.urlopen(url))
    listings = [block for section in page["primaryContentSections"] for block in section.get("content", [])
                if block.get("type") == "codeListing"]
    dtd = tmp_path / "fcpxml-1.10.dtd"
    dtd.write_text("\n".join(listings[0]["code"]) + "\n")
    printed, _ = exported(cut_project)
    done = subprocess.run(["xmllint", "--noout", "--dtdvalid", str(dtd), printed["fcpxml"]], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_graphics_layer_goes_to_resolve_as_prores_4444_above_the_cuts(cut_project):
    from clips import TITLE, comp_json, put_comp

    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].append({"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                           "start": "b:2", "out": "b:6", "params": {"colour": "rgb(200, 40, 120)"}})
    put_comp(cut_project, comp)
    printed, root = exported(cut_project)
    (graphic,) = printed["graphics"]
    assert graphic["comp_frames"] == [38, 128]
    stream = json.loads(subprocess.run([tool("ffprobe"), "-v", "error", "-count_packets", "-show_entries",
                                        "stream=nb_read_packets,pix_fmt", "-of", "json", graphic["file"]],
                                       capture_output=True, text=True, check=True).stdout)["streams"][0]
    assert int(stream["nb_read_packets"]) == 90 and stream["pix_fmt"].startswith("yuva444p")
    clip = next(c for c in root.iter("asset-clip") if c.get("name") == "title")
    assert clip.get("lane") == "1" and clip.get("start") == "0s" and clip.get("duration") == f"{90 * FRAME}/240000s"
    shot1 = root.findall("library/event/project/sequence/spine/asset-clip")[0]
    assert clip in list(shot1)  # anchored to the cut that holds its first frame, frame 38
    assert seconds(clip.get("offset")) == seconds(shot1.get("start")) + Fraction((38 - 8) * FRAME, 240000)


def test_a_remap_goes_to_fcpxml_as_a_time_map_through_its_keys(cut_project):
    from clips import comp_json, put_comp

    comp = comp_json()
    del comp["layers"][1]["source_start"]
    comp["layers"][1]["time"] = {"remap": [{"t": "b:0", "v": "f:2"}, {"t": "b:2", "v": "f:10", "ease": "out"},
                                          {"t": "b:4", "v": "f:60"}]}
    comp["layers"][2]["retime"] = "blend"
    put_comp(cut_project, comp)
    _, root = exported(cut_project)
    shot1, shot2 = root.findall("library/event/project/sequence/spine/asset-clip")
    points = shot1.find("timeMap").findall("timept")
    # The clip's first frame (frame 8, after the key at b:0), the keys at b:2 and b:4 (2.25 s,
    # inside a clip that ends on frame 68, 2.269 s), and its end.
    assert len(points) == 4
    assert [p.get("interp") for p in points] == ["linear", "smooth2", "linear", "linear"]
    values = [seconds(p.get("value")) for p in points]
    assert values == sorted(values)
    assert shot2.find("timeMap").get("frameSampling") == "frame-blending"


def test_a_constant_music_gain_goes_to_fcpxml_and_keys_are_refused(cut_project):
    from clips import comp_json, put_comp

    comp = comp_json()
    comp["layers"][0]["gain"] = -4.5
    put_comp(cut_project, comp)
    _, root = exported(cut_project)
    music = next(c for c in root.iter("asset-clip") if c.get("name") == "music")
    assert music.find("adjust-volume").get("amount") == "-4.5dB"
    comp["layers"][0]["gain"] = {"keys": [{"t": "1s", "v": 0.0}, {"t": "2s", "v": -6.0}]}
    put_comp(cut_project, comp)
    refused = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert "gain keys or fades" in str(refused.exception)
