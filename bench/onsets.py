"""SuperFlux onsets (src/lungecuta/onsets.py) on a music file's cache WAV: how many, how
far apart, and how long the detection takes.

Usage: uv run python bench/onsets.py WAV
  WAV is a 48 kHz PCM WAV as lc import writes it under cache/audio/.

There is no ground truth for a real song, so the numbers are a sanity check (a count a
second and gaps that match the music's subdivisions); `tests/test_onsets.py` checks the
detector against a synthetic drum track whose hits are known.
"""
import json
import sys
import time
from pathlib import Path

import numpy as np

from lungecuta.music import read_wav
from lungecuta.onsets import THRESHOLD, onsets


def main() -> None:
    signal, rate = read_wav(Path(sys.argv[1]))
    start = time.perf_counter()
    found = onsets(signal, rate)
    took = time.perf_counter() - start
    seconds = signal.shape[0] / rate
    gaps = np.diff(found)
    print(json.dumps({"seconds": round(seconds, 3), "rate": rate, "threshold": THRESHOLD, "onsets": len(found),
                      "per_second": round(len(found) / seconds, 2), "gap_min": round(float(gaps.min()), 3),
                      "gap_median": round(float(np.median(gaps)), 3), "detect_s": round(took, 3)}))


if __name__ == "__main__":
    main()
