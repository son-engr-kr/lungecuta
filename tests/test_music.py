"""Music import: the 48 kHz WAV, encoder delay, and the beat map on a synthetic drum track
whose beats are known (tests/clips.py)."""
import json

import numpy as np
import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from lungecuta.cli import main
from lungecuta.files import content_hash
from lungecuta.media import Project, import_file, set_root
from lungecuta.music import decode_audio, match, read_wav, tempo, to_wav_rate, wav_bytes
from lungecuta.probe import probe
from lungecuta.project import init_project
from lungecuta.schema.media import Analyzer, BeatMap, ManualBeat
from lungecuta.tools import run

from clips import beat_map, drum_track

# The beat-tracking evaluation window mir_eval uses by default (f_measure_threshold 0.07 s).
TOLERANCE = 0.07


def test_the_wav_is_float_and_reads_back_exactly(tmp_path):
    signal = np.random.default_rng(0).uniform(-1.2, 1.2, (4800, 2)).astype(np.float32)
    path = tmp_path / "a.wav"
    path.write_bytes(wav_bytes(signal, 48000))
    back, rate = read_wav(path)
    assert rate == 48000 and np.array_equal(back, signal)
    stream = json.loads(run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)]).stdout)["streams"][0]
    assert (stream["codec_name"], stream["sample_rate"], stream["channels"]) == ("pcm_f32le", "48000", 2)
    assert np.array_equal(decode_audio(path, probe(path).audio[0]), signal), "ffmpeg reads the same samples"


def test_resampling_to_48k_keeps_the_pitch():
    rate = 44100
    tone = np.sin(2 * np.pi * 1000 * np.arange(rate) / rate).astype(np.float32)[:, None]
    resampled = to_wav_rate(tone, rate)
    assert resampled.shape == (48000, 1) and resampled.dtype == np.float32
    spectrum = np.abs(np.fft.rfft(resampled[:, 0]))
    assert int(np.argmax(spectrum)) == 1000  # one-second window: bin k is k Hz
    assert to_wav_rate(tone, 48000) is tone


@pytest.mark.parametrize("codec,suffix", [("aac", ".m4a"), ("libmp3lame", ".mp3"), ("flac", ".flac")])
def test_ffmpeg_removes_the_encoder_delay(tmp_path, codec, suffix):
    signal, _, _ = drum_track(rate=44100, bars=2)
    source = tmp_path / "drums.wav"
    source.write_bytes(wav_bytes(signal, 44100))
    encoded = tmp_path / f"drums{suffix}"
    run(["ffmpeg", "-v", "error", "-i", str(source), "-c:a", codec, *([] if codec == "flac" else ["-b:a", "192k"]),
         str(encoded)])
    decoded = decode_audio(encoded, probe(encoded).audio[0])
    a, b, span = signal[:, 0], decoded[:, 0], 44100 * 3
    lags = range(-2048, 2049)
    best = max(lags, key=lambda lag: float(np.dot(a[2048:span], b[2048 + lag:span + lag])))
    assert best == 0, f"{suffix} decodes {best} samples late"


def test_a_beat_map_holds_together():
    analyzer = Analyzer(name="beat_this", version="1.1.0", checkpoint="final0", checkpoint_hash="sha256:" + "0" * 64,
                        postprocessing="minimal", device="cpu")
    good = dict(analyzer=analyzer, times=[0.5, 1.0, 1.5], downbeats=[0], manual=[])
    BeatMap(**good)
    with pytest.raises(ValidationError, match="must increase"):
        BeatMap(**{**good, "times": [0.5, 0.5, 1.5]})
    with pytest.raises(ValidationError, match="one of the beats"):
        BeatMap(**{**good, "downbeats": [3]})
    with pytest.raises(ValidationError, match="times says otherwise"):
        BeatMap(**{**good, "manual": [ManualBeat(index=1, time=1.02)]})
    assert tempo(BeatMap(**good)) == 120


def test_lc_import_finds_the_beats_of_a_drum_track(tmp_path):
    project = init_project(tmp_path / "edit.lc")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    signal, truth, downbeats = drum_track(rate=44100)
    source = music_dir / "drums.wav"
    source.write_bytes(wav_bytes(signal, 44100))
    track = music_dir / "drums.m4a"
    run(["ffmpeg", "-v", "error", "-i", str(source), "-c:a", "aac", "-b:a", "192k", str(track)])
    set_root(project, "music", music_dir)
    record = import_file(Project.load(project), track, root="music")
    assert (record.time, record.color, record.decode, record.proxy) == (None, None, None, None)
    wav = record.wav
    assert (wav.path, wav.rate, wav.channels, wav.resampled_from) == ("cache/audio/drums.wav", 48000, 2, 44100)
    assert content_hash(project / wav.path) == wav.hash
    found = np.array(record.beats.times)
    # Every beat of the track is found, within the evaluation window, one to one.
    matched = [int(np.argmin(np.abs(found - t))) for t in truth]
    assert all(abs(found[k] - t) <= TOLERANCE for k, t in zip(matched, truth))
    assert len(set(matched)) == len(truth) and matched == sorted(matched)
    assert {matched[k] for k in downbeats} <= set(record.beats.downbeats)
    on_disk = json.loads((project / "media" / "drums.json").read_text())
    assert on_disk["beats"]["analyzer"]["name"] == "beat_this" and on_disk["beats"]["manual"] == []


def test_a_reanalysis_that_moves_beats_slightly_keeps_every_index():
    current = beat_map([0.5, 1.0, 1.5, 2.0], manual=[ManualBeat(index=2, time=1.5)])
    new = beat_map([0.51, 1.01, 1.49, 2.02])
    result = match(current, new)
    assert result.keeps_every_index and not result.inserted and not result.removed
    merged = result.merged()
    assert merged.times == [0.51, 1.01, 1.5, 2.02], "the manual beat keeps its time"
    assert merged.manual == current.manual


def test_a_gained_or_lost_beat_is_listed_and_not_kept():
    current = beat_map([0.5, 1.0, 1.5, 2.0])
    pickup = match(current, beat_map([0.0, 0.5, 1.0, 1.5, 2.0], downbeats=(1,)))
    assert pickup.inserted == [0] and not pickup.removed and not pickup.keeps_every_index
    lost = match(current, beat_map([0.5, 1.5, 2.0]))
    assert lost.removed == [1] and not lost.keeps_every_index
    # Two new beats on one current beat: the nearer is the match, the other inserted.
    doubled = match(current, beat_map([0.5, 1.0, 1.2, 1.5, 2.0]))
    assert doubled.inserted == [2] and doubled.pairs[1] == 1


def test_moved_downbeats_shift_bar_literals_and_are_not_kept():
    current = beat_map([0.5, 1.0, 1.5, 2.0], downbeats=(0,))
    assert not match(current, beat_map([0.5, 1.0, 1.5, 2.0], downbeats=(1,))).keeps_every_index


def test_lc_beats_applies_an_analysis_that_keeps_every_index(tmp_path):
    project = init_project(tmp_path / "edit.lc")
    music_dir = tmp_path / "music"
    music_dir.mkdir()
    signal, _, _ = drum_track(rate=48000, bars=4)
    (music_dir / "drums.wav").write_bytes(wav_bytes(signal, 48000))
    set_root(project, "music", music_dir)
    record = import_file(Project.load(project), music_dir / "drums.wav", root="music")
    assert record.wav.resampled_from is None
    (project / record.wav.path).unlink()  # the cache was cleared: the WAV is decoded again
    result = CliRunner().invoke(main, ["beats", "drums", "--project", str(project)])
    assert result.exit_code == 0, result.output
    printed = json.loads(result.output)
    assert printed["applied"] and printed["keeps_every_index"] and printed["inserted"] == printed["removed"] == []
    assert content_hash(project / record.wav.path) == record.wav.hash
