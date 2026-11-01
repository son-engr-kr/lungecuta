"""DJI files: the colour spaces lungecuta adds from DJI's published transforms
(camera_spaces.py), detection from DJI's container tags and per-frame sensor names
(dji.py, detect.py), lens runs on records and in lint."""
import json

import numpy as np
import PyOpenColorIO as ocio
import pytest
from pydantic import ValidationError

from lungecuta import camera_spaces as cs
from lungecuta import color, detect, dji
from lungecuta.project import DEFAULT_COLOR
from lungecuta.schema.media import LensRun, MediaRecord

CONFIG = DEFAULT_COLOR.config
DGAMUT = ((0.71, 0.31), (0.21, 0.88), (0.09, -0.08), (0.3127, 0.3290))


def test_dlog2_puts_grey_and_black_on_whole_codes_and_is_continuous():
    assert cs.dlog2_to_linear(312 / 1023) == pytest.approx(0.18, abs=1e-12)
    assert cs.dlog2_to_linear(64 / 1023) == pytest.approx(0.0, abs=1e-12)
    assert cs.dlog2_to_linear(1.0) == pytest.approx(cs.H)
    for at in (cs.B1, cs.B2):
        assert cs.dlog2_to_linear(at - 1e-12) == pytest.approx(cs.dlog2_to_linear(at + 1e-12), abs=1e-9)
    x = np.linspace(-1.2, 2.2, 10001)
    assert np.abs(cs.linear_to_dlog2(cs.dlog2_to_linear(x)) - x).max() < 1e-12


def test_the_matrix_method_is_the_configs_own_and_agrees_with_djis_rounded_matrices():
    group = color.config(CONFIG).getColorSpace("Linear D-Gamut").getTransform(ocio.COLORSPACE_DIR_TO_REFERENCE)
    configs = np.array(group[0].getMatrix()).reshape(4, 4)[:3, :3]
    assert np.abs(cs.rgb_to_rgb(DGAMUT, cs.AP0) - configs).max() < 1e-12
    # DJI's DCTL: dgamut2_to_xyz, d65_to_d60_cat02 and xyz_to_acesAP0, to four decimals
    to_xyz = np.array([[0.6917, 0.1596, 0.0990], [0.2498, 0.8381, -0.0880], [0.0, 0.0, 1.0891]])
    adapt = np.array([[1.0120, 0.0080, -0.0158], [0.0058, 1.0014, -0.0063], [-0.0003, -0.0010, 0.9276]])
    to_ap0 = np.array([[1.0498, 0.0, -0.0001], [-0.4959, 1.3733, 0.0982], [0.0, 0.0, 0.9913]])
    assert np.abs(cs.rgb_to_rgb(cs.D_GAMUT2, cs.AP0) - to_ap0 @ adapt @ to_xyz).max() < 2e-4


def apply(source: str, target: str, values) -> np.ndarray:
    image = np.repeat(np.asarray(values, np.float32)[:, None], 3, axis=1).copy()
    color.cpu_processor(CONFIG, source, target).applyRGB(image)
    return image[:, 0]


def test_the_config_carries_dlog2_both_ways_as_djis_formula():
    x = np.linspace(0.0, 1.0, 20001)
    exact = cs.dlog2_to_linear(x)
    lin = apply(cs.DLOG2, cs.LINEAR_DGAMUT2, x)
    big = np.abs(exact) >= 1e-3
    assert (np.abs(lin - exact) / np.abs(exact))[big].max() < 1e-5
    assert np.abs(lin - exact)[~big].max() < 1e-8
    # the way back in the same process: a processor cache that took one LUT for the other
    # would fail here (camera_spaces.half_domain_lut)
    assert np.abs(apply(cs.LINEAR_DGAMUT2, cs.DLOG2, exact) - x).max() * 1023 < 1e-3
    assert cs.DLOG2 in [space.getName() for space in color.config(CONFIG).getColorSpaces()]


def test_the_two_luts_are_finite_and_described_apart():
    forward, inverse = cs.half_domain_lut(cs.dlog2_to_linear), cs.half_domain_lut(cs.linear_to_dlog2)
    for lut in (forward, inverse):
        assert np.isfinite(np.asarray(lut.getData())).all()
    assert str(forward) != str(inverse)


def test_a_dlog2_clip_enters_the_working_space_through_djis_curve():
    codes = np.arange(0, 1024) / 1023.0
    into = color.input_processor(DEFAULT_COLOR, cs.DLOG2)
    got = np.repeat(codes.astype(np.float32)[:, None], 3, axis=1).copy()
    into.applyRGB(got)
    want = apply(cs.LINEAR_DGAMUT2, DEFAULT_COLOR.working, cs.dlog2_to_linear(codes))
    assert np.abs(got[:, 0] - want).max() * 1023 < 2e-3


def packets(*sensors: str) -> list[bytes]:
    return [b"TPLG_VIDEO_4K_16X9_2397_KA*\x00" + name.encode() + b"_BIN2_DCG_3968_2240\x00" for name in sensors]


PP041 = dji.SENSORS["PP-041"]


def test_lens_runs_follow_the_sensor_each_frame_names():
    runs = dji.lens_runs(packets("OV50X50", "OV50X50", "IMX06C", "OV50X50"), PP041)
    assert [(r.first, r.frames, r.lens) for r in runs] == [(0, 2, "20mm F2"), (2, 1, "60mm F1.8"), (3, 1, "20mm F2")]
    assert dji.lens_runs([b"no sensor here"], PP041) == (
        "debug packet of frame 0 names none of the sensors ['IMX06C', 'OV50X50']")
    assert "names OV50X50 and IMX06C" in dji.lens_runs([b"OV50X50 IMX06C"], PP041)


def tags(mode: str, lens: str = "20mm F2") -> dji.DjiFile:
    return dji.DjiFile(encoder="DJI OsmoPocket4P", debug_stream=4, tags={
        "CameraModel": "PP-041", "ColorGammaSxS": mode, "LensType": lens, "ExposureIndexAsa": "1600",
        "WhiteBalanceKelvin": "3200", "WhiteBalanceTintCc": "5"})


STREAM = {"color_range": "tv", "color_space": "bt709", "color_transfer": "bt709", "color_primaries": "bt709"}


@pytest.fixture
def frames(monkeypatch):
    """The debug packets `_dji_color` reads, set by the test."""
    given = {}
    monkeypatch.setattr(dji, "packets", lambda path, stream: given["packets"])
    return given


def test_dlog2_on_its_lens_is_djis_space_in_the_streams_matrix_and_range(tmp_path, frames):
    frames["packets"] = packets(*["OV50X50"] * 5)
    decided, lenses = detect._dji_color(tmp_path / "DJI_0001_D.MP4", tags("D-Log2"), STREAM, 5)
    assert (decided.status, decided.space, decided.matrix, decided.range) == ("decided", cs.DLOG2, "bt709", "limited")
    assert decided.evidence["ColorGammaSxS"] == "D-Log2" and decided.evidence["lens_runs"] == 1
    assert [(r.first, r.frames) for r in lenses] == [(0, 5)]


def test_dlog_across_a_change_of_lens_is_decided_with_its_runs(tmp_path, frames):
    frames["packets"] = packets("OV50X50", "OV50X50", "IMX06C", "IMX06C", "OV50X50")
    decided, lenses = detect._dji_color(tmp_path / "DJI_0002_D.MP4", tags("D-Log"), STREAM, 5)
    assert (decided.status, decided.space) == ("decided", "D-Log D-Gamut")
    assert [r.lens for r in lenses] == ["20mm F2", "60mm F1.8", "20mm F2"]


@pytest.mark.parametrize("mode, lens, sensors, frames_, stream, reason", [
    ("D-Log M", "20mm F2", ["OV50X50"], 1, STREAM, "no mapping for dji PP-041 + D-Log M"),
    ("D-Log2", "20mm F2", ["OV50X50", "IMX06C"], 2, STREAM,
     "D-Log2 is made on the 20mm F2 lens only, and frames 1 to 1 are 60mm F1.8"),
    ("D-Log", "60mm F1.8", ["OV50X50"], 1, STREAM, "LensType says 60mm F1.8 and the first frame is 20mm F2"),
    ("D-Log", "20mm F2", ["OV50X50"], 2, STREAM, "the debug stream has 1 packets and the clip 2 frames"),
    ("D-Log2", "20mm F2", ["OV50X50"], 1, {**STREAM, "color_range": "unknown"},
     "the stream does not state its range (color_range unknown)"),
    ("D-Log2", "20mm F2", ["OV50X50"], 1, {**STREAM, "color_space": "bt2020nc"},
     "no matrix for the stream's matrix tag bt2020nc"),
])
def test_dji_evidence_that_disagrees_or_matches_no_row_is_undecided(tmp_path, frames, mode, lens, sensors, frames_,
                                                                     stream, reason):
    frames["packets"] = packets(*sensors)
    undecided, _ = detect._dji_color(tmp_path / "DJI_0003_D.MP4", tags(mode, lens), stream, frames_)
    assert undecided.status == "undecided" and undecided.reason == reason


def test_a_dji_file_without_its_debug_stream_is_undecided(tmp_path):
    found = dji.DjiFile(encoder=None, debug_stream=None, tags=tags("D-Log2").tags)
    undecided, lenses = detect._dji_color(tmp_path / "DJI_0004_D.MP4", found, STREAM, 3)
    assert undecided.status == "undecided" and "'CAM dbgi'" in undecided.reason and lenses is None


def test_lens_runs_on_a_record_cover_its_frames_in_order(cut_project):
    path = cut_project / "media" / "C0001.json"
    record = json.loads(path.read_text())
    frames = record["probe"]["video"]["frames"]

    def runs(*pairs):
        return [{"first": first, "frames": n, "sensor": sensor, "lens": sensor} for first, n, sensor in pairs]

    MediaRecord.model_validate({**record, "lenses": runs((0, 5, "A"), (5, frames - 5, "B"))})
    for bad in (runs((0, frames, "A"), (frames, 1, "B")), runs((1, frames, "A")),
                runs((0, 5, "A"), (5, frames - 5, "A"))):
        with pytest.raises(ValidationError):
            MediaRecord.model_validate({**record, "lenses": bad})


def test_lint_warns_of_a_shot_across_a_change_of_lens(cut_project):
    from lungecuta.lint import lint
    from lungecuta.media import Project

    path = cut_project / "media" / "C0001.json"
    record = json.loads(path.read_text())
    frames = record["probe"]["video"]["frames"]
    record["lenses"] = [LensRun(first=0, frames=5, sensor="OV50X50", lens="20mm F2").model_dump(),
                        LensRun(first=5, frames=frames - 5, sensor="IMX06C", lens="60mm F1.8").model_dump()]
    path.write_text(json.dumps(record))
    warnings = [w for w in lint(Project.load(cut_project), "main")["warnings"] if w["rule"] == "one_lens"]
    assert {w["layers"][0] for w in warnings} == {"shot1", "shot2"}
    assert "across a change of lens: 20mm F2 (source f:2 to f:4), 60mm F1.8 (source f:5 to" in warnings[0]["message"]

    record["lenses"] = record["lenses"][:1]
    record["lenses"][0]["frames"] = frames
    path.write_text(json.dumps(record))
    assert not [w for w in lint(Project.load(cut_project), "main")["warnings"] if w["rule"] == "one_lens"]


def test_import_refuses_djis_low_resolution_copies_and_macos_attribute_files(tmp_path):
    from lungecuta.media import Project, import_file, set_root
    from lungecuta.project import init_project

    project = init_project(tmp_path / "edit.lungecuta")
    footage = tmp_path / "card"
    footage.mkdir()
    set_root(project, "card", footage)
    for name, message in (("DJI_0001_D.LRF", "DJI's low-resolution copy of DJI_0001_D.MP4"),
                          ("._DJI_0001_D.MP4", "AppleDouble")):
        (footage / name).write_bytes(b"\x00" * 16)
        with pytest.raises(AssertionError, match=message):
            import_file(Project.load(project), footage / name, root="card")
