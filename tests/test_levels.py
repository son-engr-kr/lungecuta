"""lungecuta levels: a file's sound over time, measured (src/lungecuta/levels.py)."""
import json
import math

import numpy as np
from click.testing import CliRunner

from lungecuta.cli import main
from lungecuta.media import Project, load_record, music_wav
from lungecuta.music import read_wav

from clips import CLICK, clip_with_sound, write_record


def run(project, *args):
    result = CliRunner().invoke(main, ["levels", *args, "--project", str(project)], catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_music_is_measured_bar_by_bar_from_its_beat_map(cut_project):
    printed = run(cut_project, "track")
    record = load_record(Project.load(cut_project), "track")
    downbeats = [record.beats.times[k] for k in record.beats.downbeats]
    rows = printed["rows"]
    assert rows[0]["before"] == "the first bar" and rows[0]["to"] == round(downbeats[0], 3)
    assert [row["bar"] for row in rows[1:]] == list(range(len(downbeats)))
    signal, rate = read_wav(music_wav(Project.load(cut_project), record))
    a, b = round(downbeats[1] * rate), round(downbeats[2] * rate)
    rms = math.sqrt(float(np.mean(signal[a:b].astype(np.float64) ** 2)))
    assert rows[2]["rms_db"] == round(20 * math.log10(rms), 2)
    assert all("lufs" in row for row in rows if row["to"] - row["from"] >= 0.4)
    from lungecuta.onsets import onsets
    assert sum(row["onsets"] for row in rows) == len(onsets(signal, rate)) > 0  # each onset in one row
    beats = run(cut_project, "track", "--every", "b:2")["rows"]
    assert [row["beat"] for row in beats[1:4]] == [0, 2, 4]
    assert printed["png"].endswith("track_levels.png")


def test_a_clip_s_own_sound_is_measured_in_steps_of_seconds(cut_project):
    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    rows = run(cut_project, "C0002", "--every", "0.25s")["rows"]
    assert [row["from_seconds"] for row in rows[:3]] == [0.0, 0.25, 0.5]
    hit = next(row for row in rows if row["from"] <= CLICK < row["to"])
    assert hit["peak_db"] == round(20 * math.log10(0.5), 2)
    assert all(row["rms_db"] is None for row in rows if row is not hit)  # silence elsewhere
