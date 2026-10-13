import json

import numpy as np
import pytest
from click.testing import CliRunner

from lungecuta.audio_check import audio_check
from lungecuta.cli import main
from lungecuta.media import Project
from lungecuta.music import wav_bytes


def test_cuts_are_measured_against_the_nearest_beat(cut_project):
    report = audio_check(Project.load(cut_project), "main")
    shot1, shot2 = report["cuts"]
    # shot1 starts on beat 0 (0.25 s) and lands on frame 8 (0.267 s); shot2 on beat 4, frame 68
    assert (shot1["layer"], shot1["nearest_beat"], shot1["downbeat"]) == ("shot1", 0, True)
    assert shot1["offset_ms"] == pytest.approx((8 * 1001 / 30000 - 0.25) * 1000, abs=0.01)
    assert (shot2["nearest_beat"], shot2["downbeat"]) == (4, True)
    # the hand-written music clicks on every beat: each cut's nearest onset is its beat's click
    assert report["onsets"]["count"] >= 12 and report["onsets"]["method"].startswith("SuperFlux")
    for cut, beat in ((shot1, 0.25), (shot2, 2.25)):
        assert -0.010 - 1e-9 <= cut["nearest_onset_seconds"] - beat <= 1e-9  # the measured lead, two frames at most
        assert cut["onset_offset_ms"] == pytest.approx(cut["seconds"] * 1000 - cut["nearest_onset_seconds"] * 1000,
                                                       abs=0.01)


def test_a_rising_mix_has_a_positive_slope(cut_project):
    # the music made to rise: 0.01 at the start to 0.3 at the end
    seconds = 10
    t = np.arange(seconds * 48000) / 48000
    rising = (np.linspace(0.01, 0.3, t.size) * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    cached = cut_project / "cache" / "audio" / "track.wav"
    cached.write_bytes(wav_bytes(np.stack([rising, rising], 1), 48000))
    record = json.loads((cut_project / "media" / "track.json").read_text())
    from lungecuta.files import content_hash
    record["wav"]["hash"] = content_hash(cached)
    (cut_project / "media" / "track.json").write_text(json.dumps(record))
    report = audio_check(Project.load(cut_project), "main", "b:2", "b:10")
    beats = [row["beat"] for row in report["rms_half_beats"]]
    assert beats[0] == 2 and beats[-1] == 9.5 and len(beats) == 16
    assert report["rms_slope_db_per_beat"] > 0.5
    assert [c["layer"] for c in report["cuts"]] == ["shot2"]  # shot1's cut is before b:2


def test_lc_audio_check_prints_json(cut_project):
    result = CliRunner().invoke(main, ["audio-check", "main", "--project", str(cut_project)])
    assert result.exit_code == 0, result.output
    assert set(json.loads(result.output)) >= {"cuts", "rms_half_beats", "rms_slope_db_per_beat", "onsets"}
