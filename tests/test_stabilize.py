"""`lungecuta stabilize`: Gyroflow's clip imported as derived media of the original. Gyroflow
itself is replaced here by a stand-in that copies its input, since the test clips carry no
gyro data; the media test at the end runs the pinned Gyroflow on the S-Log3 sample."""
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta import stabilize as stab
from lungecuta.cli import main
from lungecuta.media import Project, footage_wav, load_record, original
from lungecuta.music import read_wav


def stand_in(frames_dropped: int = 0):
    """Gyroflow as a copy: its output is its input; it also leaves a project file beside
    its input, as `--export-project` does, to show where such a file lands."""
    calls = []

    def run(args):
        calls.append(args)
        source = Path(args[0])
        (source.parent / f"{source.stem}.gyroflow").write_text("{}")
        out = json.loads(args[args.index("-p") + 1])["output_path"]
        if frames_dropped:
            from lungecuta.tools import run as tool
            tool(["ffmpeg", "-v", "error", "-i", str(source), "-frames:v", "60", "-c", "copy", out])
        else:
            shutil.copyfile(source.resolve(), out)
        return ""

    return run, calls


def test_settings_lay_the_preset_and_output_over_the_mode():
    record = type("R", (), {})()
    record.key = "C0001"
    record.probe = type("P", (), {"video": type("V", (), {"pix_fmt": "yuv422p10le"})(),
                                  "audio": [type("A", (), {"codec": "pcm_s24be"})()]})()
    preset, out = stab.settings(record, "rolling-shutter", {"stabilization": {"max_zoom": 110.0}}, None)
    assert preset == {"version": 2, "stabilization": {"method": "No smoothing", "adaptive_zoom_window": -1,
                                                      "max_zoom": 110.0}}
    assert out == {"codec": "H.264/AVC", "use_gpu": False, "encoder_options": "-qp 0 -g 1",
                   "pixel_format": "yuv422p10le", "audio": True, "audio_codec": "PCM (s24be)"}
    _, out = stab.settings(record, "stabilize", None, {"codec": "H.265/HEVC", "use_gpu": True})
    assert out["codec"] == "H.265/HEVC" and out["use_gpu"] is True
    with pytest.raises(AssertionError, match="lungecuta chooses where the clip is written"):
        stab.settings(record, "stabilize", None, {"output_path": "/elsewhere.MP4"})
    record.probe.audio[0].codec = "aac"
    with pytest.raises(AssertionError, match="which Gyroflow would encode again"):
        stab.settings(record, "stabilize", None, None)
    assert stab.settings(record, "stabilize", None, {"audio": False})[1]["audio"] is False


def test_a_stabilized_clip_is_derived_media_of_its_original(cut_project, monkeypatch):
    run, calls = stand_in()
    monkeypatch.setattr(stab.gyroflow, "run", run)
    beside = sorted(p.name for p in (cut_project / "footage").iterdir())
    result = CliRunner().invoke(main, ["stabilize", "C0001", "--mode", "rolling-shutter", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)["derived"]
    assert printed["key"] == "C0001.rs" and printed["from"] == "C0001" and printed["file"].startswith("cache/lungecuta/derived/")
    assert sorted(p.name for p in (cut_project / "footage").iterdir()) == beside, "nothing written beside the original"
    preset = json.loads(calls[0][calls[0].index("--preset") + 1])
    assert preset["stabilization"]["method"] == "No smoothing"
    project = Project.load(cut_project)
    record, source = load_record(project, "C0001.rs"), load_record(project, "C0001")
    assert record.derived_from == "C0001" and record.derivation.mode == "rolling-shutter"
    assert record.derivation.original_hash == source.hash and record.derivation.version == stab.gyroflow.RELEASE.version
    assert record.color.space == source.color.space and record.color.evidence["derived_from"] == "C0001"
    assert record.time.start_tc == source.time.start_tc
    assert original(project, record).read_bytes() == original(project, source).read_bytes()  # the stand-in copied it
    # placed as any clip
    from lungecuta import ops
    ops.place(project, "main", "C0001.rs", "b:8", length="b:1")
    again = CliRunner().invoke(main, ["stabilize", "C0001", "--mode", "rolling-shutter", "--project", str(cut_project)])
    assert again.exit_code != 0 and "exists already" in str(again.exception)
    nested = CliRunner().invoke(main, ["stabilize", "C0001.rs", "--project", str(cut_project)])
    assert nested.exit_code != 0 and "stabilize the original" in str(nested.exception)
    original(project, record).unlink()
    with pytest.raises(AssertionError, match="run lungecuta stabilize C0001 again"):
        original(project, record)


def test_a_clip_missing_frames_is_refused(cut_project, monkeypatch):
    run, _ = stand_in(frames_dropped=10)
    monkeypatch.setattr(stab.gyroflow, "run", run)
    with pytest.raises(AssertionError, match="Gyroflow wrote 60 frames of C0001's 70"):
        stab.stabilize(Project.load(cut_project), "C0001", proxy=False)
    assert not (cut_project / "media" / "C0001.stab.json").exists()


@pytest.mark.media
def test_the_pinned_gyroflow_corrects_the_s_log3_sample_frame_for_frame(samples, tmp_path):
    from lungecuta.media import import_file, set_root
    from lungecuta.project import init_project

    path = Path(samples["slog3"]["path"])
    directory = init_project(tmp_path / "stab.lungecuta")
    set_root(directory, "footage", path.parent)
    project = Project.load(directory)
    import_file(project, path, root="footage", key="clip", proxy=False)
    # lossy and fast: this checks the clip, its frames, its sound and its record, not its codes
    record = stab.stabilize(project, "clip", mode="rolling-shutter", proxy=False,
                            output={"codec": "H.265/HEVC", "use_gpu": True, "bitrate": 100, "pixel_format": "P210LE"})
    source = load_record(project, "clip")
    assert record.probe.video.frames == source.probe.video.frames and record.color.status == "decided"
    ours, theirs = read_wav(footage_wav(project, record))[0], read_wav(footage_wav(project, source))[0]
    assert len(ours) >= len(theirs) and np.array_equal(ours[: len(theirs)], theirs), "the sound is the original's"
    assert sorted(p.name for p in path.parent.iterdir() if p.suffix == ".gyroflow") == []
