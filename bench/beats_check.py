"""How close Beat This! comes to known beats, and what a music import costs.

Usage: uv run python bench/beats_check.py [MUSIC]
  (inside the project, so that the lungecuta package is importable)

1. The synthetic 4/4 drum track of tests/clips.py (120 BPM, first beat at 0.25 s, 12 bars)
   at 48 kHz and at 44.1 kHz, through `lungecuta import`'s own decode, resampling and analysis:
   each true beat matched to the nearest beat found, the signed error, and whether every
   true downbeat is a downbeat found. Matching is one to one or the run fails.
2. With MUSIC, the cost of each stage of importing it: ffmpeg decode, resampling to 48 kHz
   (if needed), loading the model, the analysis. The beats found are checked to increase
   and lie inside the file.
"""
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from clips import drum_track  # noqa: E402

from lungecuta import music  # noqa: E402
from lungecuta.probe import probe  # noqa: E402


def synthetic(rate: int) -> None:
    signal, truth, downbeats = drum_track(rate=rate)
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "drums.wav"
        path.write_bytes(music.wav_bytes(signal, rate))
        decoded = music.to_wav_rate(music.decode_audio(path, probe(path).audio[0]), rate)
    found = music.analyse(decoded, music.WAV_RATE)
    times = np.array(found.times)
    matched = [int(np.argmin(np.abs(times - t))) for t in truth]
    assert len(set(matched)) == len(truth) and matched == sorted(matched), "the match is not one to one"
    error = (times[matched] - np.array(truth)) * 1000
    on_downbeats = {matched[k] for k in downbeats} <= set(found.downbeats)
    extra = len(times) - len(truth)
    print(f"  {rate} Hz: {len(truth)} beats found, {extra} more; error mean {error.mean():+.1f} ms, "
          f"min {error.min():+.1f}, max {error.max():+.1f}; every true downbeat found: {on_downbeats}")


def costs(path: Path) -> None:
    stream = probe(path).audio[0]
    start = time.perf_counter()
    signal = music.decode_audio(path, stream)
    decoded = time.perf_counter()
    signal = music.to_wav_rate(signal, stream.rate)
    resampled = time.perf_counter()
    from beat_this.inference import Audio2Beats

    tracker = Audio2Beats(checkpoint_path=music.CHECKPOINT, device=music.DEVICE)
    loaded = time.perf_counter()
    beats, downbeats = tracker(signal.astype(np.float64), music.WAV_RATE)
    analysed = time.perf_counter()
    assert np.all(np.diff(beats) > 0) and 0 <= beats[0] and beats[-1] <= signal.shape[0] / music.WAV_RATE
    seconds = signal.shape[0] / music.WAV_RATE
    print(f"  {path.name}: {seconds:.1f} s at {stream.rate} Hz, {len(beats)} beats, {len(downbeats)} downbeats; "
          f"decode {1000 * (decoded - start):.0f} ms, resample {1000 * (resampled - decoded):.0f} ms, "
          f"model load {1000 * (loaded - resampled):.0f} ms, analysis {1000 * (analysed - loaded):.0f} ms")


def main() -> None:
    print("synthetic drum track, 120 BPM:")
    for rate in (48000, 44100):
        synthetic(rate)
    if len(sys.argv) > 1:
        print("import costs:")
        costs(Path(sys.argv[1]))


if __name__ == "__main__":
    main()
