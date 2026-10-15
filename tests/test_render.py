"""Rendering a comp to a delivery file, and checking it (tests/clips.py's `cut_project`)."""
import dataclasses
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.decode import Layout, frame_rgb, to_rgb
from lungecuta.encode import PRESETS, SEMI_PLANAR, decimate, frame_bytes, quantize, ycbcr
from lungecuta.tools import tool

from clips import TITLE, comp_json, put_comp


def test_ycbcr_is_the_inverse_of_the_decode():
    rgb = np.random.default_rng(0).random((4, 6, 3), dtype=np.float32)
    y, cb, cr = ycbcr(rgb, bits=10, matrix="bt709")
    np.testing.assert_allclose(to_rgb(y, cb, cr, bits=10, matrix="bt709", range_="limited"), rgb, atol=1e-5)


def test_decimated_chroma_sits_where_the_file_says():
    ramp = np.tile(np.arange(8, dtype=np.float32), (4, 1))
    half = decimate(ramp, Layout.of("yuv422p10le"), "left")
    assert half[0].tolist() == [0.25, 2, 4, 6]  # co-sited: [1, 2, 1] / 4 around 0, 2, 4, 6 (edge repeated)
    rows = np.tile(np.arange(4, dtype=np.float32)[:, None], (1, 4))
    quarter = decimate(rows, Layout.of("yuv420p"), "left")
    assert quarter[:, 0].tolist() == [0.5, 2.5]  # vertically between rows 0 and 1, 2 and 3
    with pytest.raises(AssertionError, match="not written yet"):
        decimate(ramp, Layout.of("yuv422p10le"), "center")


def test_dither_adds_no_bias():
    plane = np.full((256, 256), 100.3, np.float32)
    codes = quantize(plane, 8, np.random.default_rng(1)).astype(np.float64)
    assert abs(codes.mean() - 100.3) < 0.01 and set(np.unique(codes)) <= {99.0, 100.0, 101.0}
    assert np.all(quantize(plane, 8, None) == 100)


def planar(data: bytes, size: tuple[int, int]) -> bytes:
    """P010 (Y, then Cb and Cr interleaved, codes in the top ten bits) as yuv420p10le."""
    width, height = size
    words = np.frombuffer(data, "<u2") >> 6
    chroma = words[width * height:].reshape(-1, 2)
    return words[:width * height].tobytes() + chroma[:, 0].tobytes() + chroma[:, 1].tobytes()


def test_p010_carries_the_planar_codes_in_its_top_bits():
    frame = np.random.default_rng(4).random((36, 64, 3), dtype=np.float32)
    packed, _ = frame_bytes(frame, PRESETS["web-hevc10"], seed=3)
    flat, _ = frame_bytes(frame, dataclasses.replace(PRESETS["web-hevc10"], pix_fmt="yuv420p10le"), seed=3)
    assert len(packed) == len(flat) and planar(packed, (64, 36)) == flat
    assert not np.any(np.frombuffer(packed, "<u2") & 0x3F)  # the low six bits are zero


def test_a_smooth_frame_survives_the_encode_planes():
    y, x = np.mgrid[0:36, 0:64].astype(np.float32)
    smooth = np.stack([x / 63, y / 35, np.full_like(x, 0.5)], -1)
    for name in PRESETS:
        preset = PRESETS[name]
        given = np.concatenate([smooth, np.ones_like(smooth[..., :1])], -1) if preset.alpha else smooth
        data, outside = frame_bytes(given, preset, seed=0)
        if preset.pix_fmt in SEMI_PLANAR:
            data = planar(data, (64, 36))
        back = frame_rgb(data, (64, 36), pix_fmt=SEMI_PLANAR.get(preset.pix_fmt, preset.pix_fmt),
                         chroma_location="left", matrix="bt709", range_="limited")
        assert outside == 0
        assert abs(float(((back - smooth) * 1023).mean())) < 0.2, name


def test_clipped_pixels_are_counted_not_hidden():
    frame = np.full((4, 4, 3), 0.5, np.float32)
    frame[0, 0] = (1.2, 0.5, 0.5)
    _, outside = frame_bytes(frame, PRESETS["master-prores-hq"], seed=0)
    assert outside == 1 / 16


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_render_writes_a_file_verify_accepts(cut_project, preset):
    result = CliRunner().invoke(main, ["render", "main", "--preset", preset, "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["frames"] == 188
    manifest = json.loads(open(printed["manifest"]).read())
    assert manifest["preset"] == preset and manifest["comp"]["frames"] == 188
    assert {"lungecuta", "opencolorio", "ffmpeg"} <= set(manifest["versions"])
    checked = CliRunner().invoke(main, ["verify", printed["file"], "--comp", "main", "--preset", preset,
                                        "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output
    report = json.loads(checked.output)
    assert report["ok"] and all(check["ok"] for check in report["checks"])
    assert max(abs(b) for b in report["picture"]["bias"]) < 1
    if not PRESETS[preset].audio:  # the alpha preset carries picture only
        assert report["loudness"] == {"none": "the preset has no audio"}
        return
    assert report["loudness"]["integrated_lufs"] == pytest.approx(-14.0, abs=0.2)
    assert report["loudness"]["true_peak_dbtp"] <= -1.0
    assert manifest["audio"]["loudness_after"] == pytest.approx(-14.0, abs=0.01)


def test_lossy_audio_is_muxed_as_measured_and_in_time(cut_project):
    import subprocess

    from lungecuta.audio import true_peak_db
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.mixdown import mastered

    result = CliRunner().invoke(main, ["render", "main", "--preset", "web-h264", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", printed["file"], "-map", "0:a:0", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    decoded = np.frombuffer(raw, np.float32).reshape(-1, 2)
    assert true_peak_db(decoded, 48000) == pytest.approx(printed["audio"]["delivered_true_peak"], abs=0.005)
    assert printed["audio"]["delivered_true_peak"] <= -1.0
    project = Project.load(cut_project)
    reference, _ = mastered(project, resolve(project, load_comp(project, "main")), PRESETS["web-h264"])
    piece = reference[24000:72000, 0]
    lags = range(-3000, 3001)
    best = max(lags, key=lambda k: float(np.dot(piece, decoded[24000 + k:24000 + k + piece.size, 0])))
    assert best == 0  # the AAC's priming is trimmed after the mux too


def test_verify_catches_a_cut_a_frame_off(cut_project):
    result = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    rendered = json.loads(result.output)["file"]
    comp = comp_json()
    comp["layers"][1]["in"] = "f:1"  # shot1 now starts a frame later than the file does
    put_comp(cut_project, comp)
    checked = CliRunner().invoke(main, ["verify", rendered, "--comp", "main", "--preset", "master-prores-hq",
                                        "--project", str(cut_project)])
    assert checked.exit_code == 1
    report = json.loads(checked.output)
    assert not report["ok"]
    assert any(not sample["ok"] for sample in report["picture"]["sampled"])


def test_a_mix_over_full_scale_is_mastered_under_the_ceiling(cut_project):
    from lungecuta.audio import true_peak_db
    from lungecuta.comp import load_comp, resolve
    from lungecuta.encode import PRESETS
    from lungecuta.media import Project
    from lungecuta.mixdown import mastered, mix

    comp = comp_json()
    for k in range(4):  # the track four times over itself: its clicks sum past full scale
        comp["layers"].append({"id": f"again{k}", "type": "audio", "media": "track", "start": "0s", "out": "5s",
                               "source_start": "0s"})
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    assert np.abs(mix(project, resolved)).max() > 1.0
    signal, chain = mastered(project, resolved, PRESETS["web-h264"])
    assert true_peak_db(signal, 48000) <= -1.0 and chain["loudness_after"] == pytest.approx(-14.0, abs=0.01)


def test_a_graphics_layer_is_drawn_over_the_footage_with_its_own_codes(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import frame_at

    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].append({"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                           "start": "b:2", "out": "b:6", "params": {"colour": "rgb(200, 40, 120)"}})
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    (overlay,) = resolved.overlays
    assert (overlay.first, overlay.end) == (38, 128)  # b:2 is 1.25 s (37.46 frames), b:8 is 4.25 s (127.37)
    frame = frame_at(project, resolved, 60)
    np.testing.assert_allclose(frame[10, 10] * 255, [200, 40, 120], atol=1e-3)
    seconds = overlay.layer_seconds(60, resolved.rate)
    beneath = frame_at(project, dataclasses.replace(resolved, overlays=[]), 60)
    expected = 255 * min(1, seconds) / 255 + beneath[50, 70] * (1 - min(1, seconds))
    np.testing.assert_allclose(frame[50, 70], expected, atol=0.6 / 255)  # the page's own 8-bit rounding of alpha
    result = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main", "--preset",
                                        "master-prores-hq", "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output


def test_several_graphics_layers_are_drawn_on_one_frame(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import frame_at

    (cut_project / "graphics" / "title.html").write_text(TITLE)
    (cut_project / "graphics" / "lower.html").write_text(TITLE.replace("fillRect(4, 4, 40, 20)", "fillRect(4, 48, 40, 20)"))
    comp = comp_json()
    comp["layers"] += [
        {"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display", "start": "b:2",
         "out": "b:6", "params": {"colour": "rgb(200, 40, 120)"}},
        {"id": "lower", "type": "graphics", "page": "graphics/lower.html", "space": "display", "start": "b:2",
         "out": "b:6", "params": {"colour": "rgb(30, 160, 90)"}},
        {"id": "again", "type": "graphics", "page": "graphics/title.html", "space": "display", "start": "b:8",
         "out": "b:2", "params": {"colour": "rgb(10, 20, 250)"}},
    ]
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    assert len(resolved.overlays) == 3
    frame = frame_at(project, resolved, 60)
    np.testing.assert_allclose(frame[10, 10] * 255, [200, 40, 120], atol=1e-3)
    np.testing.assert_allclose(frame[55, 10] * 255, [30, 160, 90], atol=1e-3)
    # the same page twice, with its own parameters each time
    np.testing.assert_allclose(frame_at(project, resolved, 140)[10, 10] * 255, [10, 20, 250], atol=1e-3)
    result = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main", "--preset",
                                        "master-prores-hq", "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output


def test_graphics_under_footage_composite_in_the_working_space(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import frame_at

    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].insert(1, {"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                              "start": "b:0", "out": "b:6", "params": {"colour": "rgb(200, 40, 120)"}})
    comp["layers"][2]["transform"] = {"scale": 0.5}  # shot1 over the title, covering only the middle
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    (overlay,) = resolved.overlays
    assert not overlay.top
    frame = frame_at(project, resolved, 30)
    # outside the footage the title's box comes through the inverse view and back
    np.testing.assert_allclose(frame[10, 10] * 255, [200, 40, 120], atol=1.0)
    # inside it the footage covers the title
    alone = comp_json()
    alone["layers"][1]["transform"] = {"scale": 0.5}
    put_comp(cut_project, alone)
    project = Project.load(cut_project)
    without = frame_at(project, resolve(project, load_comp(project, "main")), 30)
    np.testing.assert_array_equal(frame[30:42, 50:78], without[30:42, 50:78])
    put_comp(cut_project, comp)
    refused = CliRunner().invoke(main, ["fcpxml", "main", "--project", str(cut_project)])
    assert "under footage" in str(refused.exception)


def test_a_transform_places_and_animates_the_layer(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import frame_at

    comp = comp_json()
    comp["layers"][1]["transform"] = {
        "scale": 0.5,
        "position": {"keys": [{"t": "b:0", "v": [0.25, 0.5]}, {"t": "b:4", "v": [0.75, 0.5], "ease": "out"}]},
        "opacity": 0.8,
    }
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    cut = resolved.cuts[0]
    position, scale, opacity = cut.placement.at(cut.first * 23543520)
    assert scale == (0.5, 0.5) and opacity == 0.8 and 0.25 <= position[0] < 0.26
    untransformed = dataclasses.replace(resolved, cuts=[dataclasses.replace(cut, placement=None)] + resolved.cuts[1:])
    # The same frame, placed by hand from the untransformed render's working-space frame,
    # would need the view inverted; compare the alpha instead: outside the layer is black.
    frame = frame_at(project, resolved, 40)
    assert frame[:, :8].max() < 1e-6, "left of a half-size layer at a quarter of the width is empty"
    assert frame_at(project, untransformed, 40)[:, :8].max() > 0.05
    result = CliRunner().invoke(main, ["render", "main", "--preset", "web-h264", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main", "--preset",
                                        "web-h264", "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output


def test_a_9_16_comp_from_16_9_footage_crops_the_middle(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import frame_at

    comp = comp_json(size=[40, 72])
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    narrow = frame_at(project, resolved, 40)
    wide = frame_at(project, resolve(project, load_comp(project, "main").model_copy(update={"size": (128, 72)})), 40)
    np.testing.assert_allclose(narrow, wide[:, 44:84], atol=1e-5)


def test_overlapping_layers_composite_in_stack_order(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.compose import composite, frame_at

    comp = comp_json()
    comp["layers"][2]["start"] = "b:3"  # shot2 starts a beat before shot1 ends, over it
    comp["layers"][2]["transform"] = {"opacity": 0.5}
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    assert [cut.layer.id for cut in resolved.stack(60)] == ["shot1", "shot2"]
    below = np.full((2, 2, 3), 0.2, np.float32)
    above = np.full((2, 2, 3), 0.6, np.float32)
    mixed = composite([(below, 1.0), (above * 0.5, np.full((2, 2, 1), 0.5, np.float32))], (2, 2))
    np.testing.assert_allclose(mixed, 0.4, atol=1e-6)  # half of each, in the working space
    result = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main", "--preset",
                                        "master-prores-hq", "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output
    assert frame_at(project, resolved, 60).shape == (72, 128, 3)


def test_a_blend_mixes_two_frames_in_linear_light(cut_project):
    from lungecuta import color
    from lungecuta.media import Project
    from lungecuta.compose import blended

    project = Project.load(cut_project)
    settings = project.file.color
    a, b = np.full((2, 2, 3), 0.2, np.float32), np.full((2, 2, 3), 0.6, np.float32)
    mixed = blended(project, [(a.copy(), 0.5), (b.copy(), 0.5)])
    to_linear = color.cpu_processor(settings.config, settings.working, settings.linear)
    linear = [x.copy() for x in (a, b, mixed)]
    for x in linear:
        color.apply(to_linear, x)
    np.testing.assert_allclose(linear[2], (linear[0] + linear[1]) / 2, rtol=1e-4)
    # DI is log-like, so the light of the brighter frame dominates: the mean lies above the codes' mean.
    assert mixed[0, 0, 0] > 0.4


@pytest.mark.parametrize("keys", [
    [{"t": "b:0", "v": "f:2"}, {"t": "b:4", "v": "f:60", "ease": "in_out"}],
    [{"t": "b:0", "v": "f:60"}, {"t": "b:4", "v": "f:0"}],  # backwards: read by a seek per frame
])
def test_a_remapped_and_blended_comp_renders_and_verifies(cut_project, keys):
    comp = comp_json()
    del comp["layers"][1]["source_start"]
    comp["layers"][1]["time"] = {"remap": keys}
    comp["layers"][2]["retime"] = "blend"
    put_comp(cut_project, comp)
    result = CliRunner().invoke(main, ["render", "main", "--preset", "master-prores-hq", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main", "--preset",
                                        "master-prores-hq", "--project", str(cut_project)])
    assert checked.exit_code == 0, checked.output


def test_audio_gain_and_fades_shape_the_mix(cut_project):
    from lungecuta.comp import load_comp, resolve
    from lungecuta.media import Project
    from lungecuta.mixdown import mix

    comp = comp_json()
    comp["layers"][0] |= {"start": "0.25s", "in": "0.25s", "out": "5s"}  # heard from 0.5 s, the first beat, to 5.25 s
    comp["end"] = "5.5s"
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    plain = mix(project, resolve(project, load_comp(project, "main")))
    comp["layers"][0] |= {"gain": -6.0, "fade_in": "b:1", "fade_out": "1s"}
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    resolved = resolve(project, load_comp(project, "main"))
    shaped = mix(project, resolved)
    sound = resolved.sounds[0]
    assert (sound.first, sound.end) == (24000, 252000)
    assert sound.fade_in == 24000 and sound.fade_out == 48000  # one beat at 120 BPM; one second
    middle = slice(3 * 48000, 4 * 48000)
    assert np.allclose(shaped[middle], plain[middle] * 10 ** (-6 / 20), atol=1e-6)
    assert not shaped[sound.first].any()
    half = sound.first + 12000  # half way through the fade in: half the amplitude
    assert np.allclose(shaped[half], plain[half] * 0.5 * 10 ** (-6 / 20), atol=1e-6)
    assert np.abs(shaped[sound.end - 10:sound.end]).max() < 0.01 * np.abs(plain).max()

    comp["layers"][0] |= {"gain": {"keys": [{"t": "1s", "v": 0.0}, {"t": "2s", "v": -60.0}]}}
    del comp["layers"][0]["fade_in"], comp["layers"][0]["fade_out"]
    put_comp(cut_project, comp)
    project = Project.load(cut_project)
    keyed = mix(project, resolve(project, load_comp(project, "main")))
    assert np.allclose(keyed[:60000], plain[:60000])  # before the first key, its value: 0 dB
    assert np.abs(keyed[3 * 48000:]).max() <= 0.001 * np.abs(plain).max() + 1e-7  # after the last, -60 dB


def test_the_alpha_preset_is_transparent_where_nothing_covers(cut_project):
    from lungecuta.decode import read_raw

    comp = comp_json()
    comp["layers"][1]["transform"] = {"scale": 0.5}
    put_comp(cut_project, comp)
    args = ["--project", str(cut_project)]
    result = CliRunner().invoke(main, ["render", "main", "--preset", "prores-4444-alpha", *args])
    assert result.exit_code == 0, result.output
    data = read_raw(Path(json.loads(result.output)["file"]), 30, rate=Fraction(30000, 1001), size=(128, 72),
                    pix_fmt="yuva444p12le")
    alpha = np.frombuffer(data, "<u2")[3 * 128 * 72:].reshape(72, 128)
    assert alpha[0, 0] == 0 and alpha[36, 64] == 4095  # the corner is uncovered, the middle covered
    # half the size, centred: rows 18 to 53 and columns 32 to 95 exactly, nothing partial
    assert (alpha[18:54, 32:96] == 4095).all() and alpha[17].max() == 0 and alpha[:, 31].max() == 0
    checked = CliRunner().invoke(main, ["verify", json.loads(result.output)["file"], "--comp", "main",
                                        "--preset", "prores-4444-alpha", *args])
    assert checked.exit_code == 0, checked.output
