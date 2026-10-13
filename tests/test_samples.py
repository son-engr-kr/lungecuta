"""Tests on the sample media (`uv run pytest -m media`; see conftest.py)."""
import json
import xml.etree.ElementTree as ET
from fractions import Fraction

import numpy as np
import PyOpenColorIO as ocio
import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.decode import frame_rgb, read_rgb
from lungecuta.fcpxml import timecode_frames, timecode_text
from lungecuta.files import content_hash
from lungecuta.geometry import oriented, turns
from lungecuta.media import Project, import_file, set_root
from lungecuta.music import decode_audio, read_wav
from lungecuta.probe import probe
from lungecuta.project import DEFAULT_COLOR, init_project
from lungecuta.scope import scope_numbers
from lungecuta.still import code_numbers, render
from lungecuta.tools import run

pytestmark = pytest.mark.media

NTSC = Fraction(30000, 1001)


def test_detect_decides_the_slog3_sample(samples):
    result = CliRunner().invoke(main, ["detect", str(samples["slog3"]["path"])])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    stem = samples["slog3"]["path"].stem
    # The record in docs/design/03-project-format.md#media, with the file's own name.
    assert printed["color"] == {
        "status": "decided", "space": "S-Log3 S-Gamut3.Cine", "matrix": "bt709", "range": "full",
        "evidence": {
            "source": "sony-xml", "file": f"{stem}M01.XML", "CaptureGammaEquation": "s-log3-cine",
            "CaptureColorPrimaries": "s-gamut3-cine", "CodingEquations": "rec709",
            "stream_tags": {"color_range": "pc", "color_space": "unknown", "color_transfer": "unknown",
                            "color_primaries": "unknown"},
        },
    }
    assert printed["time"] == {
        "status": "decided", "rate": "30000/1001", "capture_rate": "30000/1001", "start_tc": "00:00:21:15",
        "evidence": {"source": "sony-xml", "file": f"{stem}M01.XML", "captureFps": "29.97p",
                     "formatFps": "29.97p", "RecordingMode": "normal", "LtcChange": "55210000"},
    }


def test_detect_leaves_the_hlg_sample_undecided(samples):
    result = CliRunner().invoke(main, ["detect", str(samples["hlg"]["path"])])
    assert result.exit_code == 1
    color = json.loads(result.output)["color"]
    assert color["status"] == "undecided"
    assert color["reason"] == "no mapping for sony-xml hlg-slog3ootf + rec2020"
    assert color["evidence"]["CodingEquations"] == "rec2020ncl"
    assert color["evidence"]["stream_tags"] == {
        "color_range": "tv", "color_space": "bt2020nc", "color_transfer": "arib-std-b67", "color_primaries": "bt2020",
    }


def sequential(path, index, *, matrix, range_, size, autorotate=False):
    """Frame `index` from a decode from the start, independent of lc's seeking."""
    raw = run(["ffmpeg", "-v", "error", *([] if autorotate else ["-noautorotate"]), "-i", str(path),
               "-map", "0:v:0", "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", "-f", "rawvideo",
               "-pix_fmt", "yuv422p10le", "-"]).stdout
    return frame_rgb(raw, size, pix_fmt="yuv422p10le", chroma_location="left", matrix=matrix, range_=range_)


@pytest.mark.parametrize("index", [0, 1, 13, 352, 704])
def test_a_seeked_frame_is_the_frame_a_sequential_decode_gives(samples, index):
    path = samples["slog3"]["path"]
    seeked = read_rgb(path, index, rate=NTSC, size=(3840, 2160), pix_fmt="yuv422p10le", matrix="bt709",
                      range_="full", chroma_location="left")
    assert np.array_equal(seeked, sequential(path, index, matrix="bt709", range_="full", size=(3840, 2160)))


@pytest.fixture(scope="module")
def imported(samples, tmp_path_factory):
    project = init_project(tmp_path_factory.mktemp("samples") / "edit.lc")
    records = {}
    for role in ("slog3", "hlg"):
        path = samples[role]["path"]
        root = path.parent
        name = f"{role}-root"
        set_root(project, name, root)
        records[role] = import_file(Project.load(project), path, root=name)
    return project, records


def test_import_writes_the_documented_slog3_record(samples, imported):
    project, records = imported
    record = records["slog3"]
    path = samples["slog3"]["path"]
    on_disk = json.loads((project / "media" / f"{path.stem}.json").read_text())
    assert on_disk == record.model_dump(mode="json")
    assert record.key == path.stem
    assert record.path.rel == path.name
    assert record.hash == content_hash(path)
    probe = on_disk["probe"]
    assert probe == {
        "video": {"codec": "h264", "profile": "High 4:2:2", "pix_fmt": "yuv422p10le", "size": [3840, 2160],
                  "rate": "30000/1001", "frames": 705,
                  "tags": {"color_range": "pc", "color_space": "unknown", "color_transfer": "unknown",
                           "color_primaries": "unknown"},
                  "chroma_location": "left", "rotation": 0},
        "audio": [{"codec": "pcm_s24be", "channels": 2, "layout": "stereo", "rate": 48000}],
        "data_streams": 1,
        "timecode": "00:00:21:15",
        "creation_time": samples["slog3"]["creation_time"],
    }
    # VideoToolbox drops frames of 4:2:2 10-bit H.264 (D6), so the decoder is software.
    assert on_disk["decode"]["decoder"] == "software"
    assert on_disk["decode"]["checked"]["result"] in ("failed", "different")
    proxy = on_disk["proxy"]
    assert (proxy["size"], proxy["range"], proxy["matrix"]) == ([960, 540], "full", "bt709")
    assert (project / proxy["path"]).is_file()
    assert proxy["parity"]["codes"]["p99"] <= 2
    assert proxy["parity"]["di"] is not None


def test_import_keeps_the_hlg_sample_undecided_in_its_coded_orientation(imported):
    project, records = imported
    record = records["hlg"]
    assert record.color.status == "undecided"
    assert record.probe.video.rotation == 90
    assert record.proxy.size == (960, 540) and record.proxy.range == "limited"
    assert record.proxy.parity.codes is None and "undecided" in record.proxy.parity.reason


def test_lc_still_on_the_slog3_sample_is_the_frame_through_its_input_transform_and_the_view(samples, imported):
    project, records = imported
    key = records["slog3"].key
    result = CliRunner().invoke(main, ["still", "--media", key, "--at", "f:352", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    # The same chain built from the OCIO config directly, on a sequentially decoded frame.
    frame = sequential(samples["slog3"]["path"], 352, matrix="bt709", range_="full", size=(3840, 2160))
    config = ocio.Config.CreateFromBuiltinConfig(DEFAULT_COLOR.config)
    config.getProcessor("S-Log3 S-Gamut3.Cine", DEFAULT_COLOR.working).getDefaultCPUProcessor().applyRGB(frame)
    view = ocio.DisplayViewTransform()
    view.setSrc(DEFAULT_COLOR.working)
    view.setDisplay(DEFAULT_COLOR.display)
    view.setView(DEFAULT_COLOR.view)
    config.getProcessor(view).getDefaultCPUProcessor().applyRGB(frame)
    assert printed["codes"] == code_numbers(frame)
    assert (printed["size"], printed["rotation"], printed["space"]) == ([3840, 2160], 0, "S-Log3 S-Gamut3.Cine")
    raw = run(["ffmpeg", "-v", "error", "-i", printed["png"], "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
    eight = np.floor(np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8)
    assert np.array_equal(np.frombuffer(raw, np.uint8).reshape(2160, 3840, 3), eight)


def test_lc_still_refuses_the_undecided_hlg_sample(imported):
    project, records = imported
    result = CliRunner().invoke(main, ["still", "--media", records["hlg"].key, "--at", "f:0", "--project", str(project)])
    assert result.exit_code != 0
    assert "colour space is undecided" in str(result.exception)


def test_the_hlg_samples_rotation_tag_turns_it_the_way_ffmpeg_does(samples):
    path = samples["hlg"]["path"]
    rotation = probe(path).video.rotation
    assert rotation == 90
    coded = read_rgb(path, 0, rate=NTSC, size=(3840, 2160), pix_fmt="yuv422p10le", matrix="bt2020nc",
                     range_="limited", chroma_location="left")
    shown_by_ffmpeg = sequential(path, 0, matrix="bt2020nc", range_="limited", size=(2160, 3840), autorotate=True)
    distance = {k: np.abs(np.rot90(coded, k) - shown_by_ffmpeg).mean() for k in (1, 3)}
    assert min(distance, key=distance.get) == turns(rotation)
    assert np.array_equal(oriented(coded, rotation), np.rot90(coded, 1))


def test_lc_sheet_on_the_slog3_sample_tiles_the_stills(imported):
    project, records = imported
    record = records["slog3"]
    result = CliRunner().invoke(main, ["sheet", "--media", record.key, "--every", "f:30", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert len(printed["pages"]) == 1 and printed["size"] == [320, 180]
    assert [tile["frame"] for tile in printed["tiles"]] == list(range(0, 705, 30))
    # One sequential decode gives the frames a seek per frame gives.
    for tile in (printed["tiles"][0], printed["tiles"][12], printed["tiles"][-1]):
        still = render(Project.load(project), record, tile["frame"], height=180)
        assert tile["codes"] == code_numbers(still)


def test_lc_scope_on_the_slog3_sample_prints_the_numbers_of_the_still(imported):
    project, records = imported
    record = records["slog3"]
    result = CliRunner().invoke(main, ["scope", "--media", record.key, "--at", "f:352", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    expected = scope_numbers(render(Project.load(project), record, 352))
    assert {name: printed[name] for name in expected} == expected
    assert printed["size"] == [3840, 2160]


@pytest.fixture(scope="module")
def imported_music(samples, tmp_path_factory):
    project = init_project(tmp_path_factory.mktemp("music") / "edit.lc")
    path = samples["music"]["path"]
    set_root(project, "music", path.parent)
    return project, import_file(Project.load(project), path, root="music")


def test_import_decodes_the_music_sample_once_and_finds_its_beats(samples, imported_music):
    project, record = imported_music
    audio = record.probe.audio[0]
    wav, signal_rate = read_wav(project / record.wav.path)
    assert signal_rate == 48000 and wav.shape == (record.wav.samples, audio.channels)
    # ffmpeg's decode, untouched: the file is at 48 kHz already.
    assert record.wav.resampled_from is None
    assert np.array_equal(wav, decode_audio(samples["music"]["path"], audio))
    beats = record.beats
    assert 0 <= beats.times[0] and beats.times[-1] <= record.wav.samples / 48000
    assert len(beats.downbeats) >= 2 and set(beats.downbeats) <= set(range(len(beats.times)))


def test_reanalysing_the_music_sample_keeps_every_index(imported_music):
    project, record = imported_music
    result = CliRunner().invoke(main, ["beats", record.key, "--project", str(project)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["keeps_every_index"]


def test_a_cut_of_the_samples_exports_with_the_clips_timecode(samples, imported):
    project, records = imported
    set_root(project, "music", samples["music"]["path"].parent)
    record = import_file(Project.load(project), samples["music"]["path"], root="music")
    key = records["slog3"].key
    comp = {
        "id": "cut", "size": [3840, 2160], "rate": "30000/1001", "end": "b:20",
        "beats": {"from_layers": ["music"]},
        "markers": [{"t": "b:12", "name": "slow"}],
        "layers": [
            {"id": "music", "type": "audio", "media": record.key, "start": "0s", "out": "30s", "source_start": "0s"},
            {"id": "a", "type": "footage", "media": key, "start": "b:4", "out": "b:8", "source_start": "f:100"},
            {"id": "b", "type": "footage", "media": key, "start": "b:12", "out": "b:8", "source_start": "f:400",
             "time": {"speed": 0.5}},
        ],
    }
    (project / "comps" / "cut.json").write_text(json.dumps(comp))
    settings = json.loads((project / "project.json").read_text())
    settings["comps"].append("comps/cut.json")
    (project / "project.json").write_text(json.dumps(settings))
    result = CliRunner().invoke(main, ["fcpxml", "cut", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    a, b = printed["cuts"]
    assert a["source_tc_in"] == timecode_text(timecode_frames("00:00:21:15", NTSC) + a["source_frames"][0], NTSC)
    assert a["source_frames"][0] in (100, 101) and b["speed"] == 0.5
    root = ET.parse(printed["fcpxml"]).getroot()
    asset = root.find("resources/asset[@name='%s']" % key)
    assert Fraction(asset.get("start")[:-1]) == Fraction(645 * 1001, 30000)


def test_a_short_cut_of_the_samples_renders_and_verifies(samples, imported):
    project, records = imported
    if "music" not in json.loads((project / "project.json").read_text())["media_roots"]:
        set_root(project, "music", samples["music"]["path"].parent)
    if not (project / "media" / f"{samples['music']['path'].stem}.json").exists():
        import_file(Project.load(project), samples["music"]["path"], root="music")
    music = samples["music"]["path"].stem
    key = records["slog3"].key
    comp = {
        "id": "short", "size": [3840, 2160], "rate": "30000/1001", "end": "f:40",
        "layers": [
            {"id": "music", "type": "audio", "media": music, "start": "0s", "out": "2s", "source_start": "10s"},
            {"id": "a", "type": "footage", "media": key, "start": "f:0", "out": "f:20", "source_start": "f:100"},
            {"id": "b", "type": "footage", "media": key, "start": "f:20", "out": "f:20", "source_start": "f:500"},
        ],
    }
    (project / "comps" / "short.json").write_text(json.dumps(comp))
    settings = json.loads((project / "project.json").read_text())
    settings["comps"].append("comps/short.json")
    (project / "project.json").write_text(json.dumps(settings))
    result = CliRunner().invoke(main, ["render", "short", "--preset", "master-prores-hq", "--project", str(project)])
    assert result.exit_code == 0, result.output
    rendered = json.loads(result.output)
    checked = CliRunner().invoke(main, ["verify", rendered["file"], "--comp", "short", "--preset", "master-prores-hq",
                                        "--project", str(project)])
    assert checked.exit_code == 0, checked.output
    report = json.loads(checked.output)
    assert report["ok"] and report["picture"]["psnr_db"] > 40
