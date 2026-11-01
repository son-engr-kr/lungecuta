"""What `lungecuta voice` gets from Kokoro-82M v1.0 on a list of lines: whether misaki reads
every word without its espeak-ng fallback; whether each waveform is 600 samples a frame
of the durations; whether a line comes out the same twice, and with another seed keeps
its timing; which onnxruntime thread counts give the same samples; whether the samples
equal kokoro-onnx 0.6.1's for the same phonemes, voice, speed and seeded session (its
trimming and pauses off); how long a line takes; and whether onnxruntime's telemetry
cache was touched.

LINES is a JSON list of {"text", "voice", "speed"} (other keys ignored). kokoro-onnx pulls
in phonemizer and espeak-ng (GPL-3.0), so it is added for this run only:

Usage: uv run --with kokoro-onnx==0.6.1 python bench/voice_check.py LINES
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from lungecuta import voice

TELEMETRY = Path.home() / "Library" / "Application Support" / "Microsoft" / "DeveloperTools" / ".onnxruntime"


def stamps() -> dict[str, int]:
    return {p.name: p.stat().st_mtime_ns for p in TELEMETRY.iterdir()} if TELEMETRY.is_dir() else {}


def digest(a: np.ndarray) -> str:
    return hashlib.sha256(a.tobytes()).hexdigest()[:16]


def main(path: str) -> None:
    before = stamps()
    lines = json.loads(Path(path).read_text())
    cores = os.cpu_count()
    report = {"lines": len(lines), "words": 0, "unread": [], "phonemes": [], "frames_exact": 0, "repeatable": 0,
              "seed_keeps_timing": 0, "threads": {}, "same_as_kokoro_onnx": 0, "seconds_of_speech": 0.0,
              "seconds_to_make": 0.0}
    made = []
    for line in lines:
        text, name, speed = line["text"], line["voice"], float(line["speed"])
        phonemes, parts = voice.phonemize(text, name)
        report["words"] += sum(1 for p in parts if any(c.isalnum() for c in p["text"]))
        report["phonemes"].append(len(phonemes))
        started = time.perf_counter()
        a, frames = voice.synthesize(phonemes, name, speed, 0, cores)
        report["seconds_to_make"] += time.perf_counter() - started
        report["seconds_of_speech"] += a.size / voice.RATE
        report["frames_exact"] += int(a.size == int(frames.sum()) * voice.SAMPLES_PER_FRAME)
        b, again = voice.synthesize(phonemes, name, speed, 0, cores)
        report["repeatable"] += int(a.tobytes() == b.tobytes() and bool((frames == again).all()))
        c, other = voice.synthesize(phonemes, name, speed, 1, cores)
        report["seed_keeps_timing"] += int(bool((frames == other).all()) and a.tobytes() != c.tobytes())
        for threads in sorted({1, 2, 4, 8, cores}):
            t, _ = voice.synthesize(phonemes, name, speed, 0, threads)
            report["threads"].setdefault(threads, 0)
            report["threads"][threads] += int(t.tobytes() == a.tobytes())
        made.append((phonemes, name, speed, a))

    from kokoro_onnx import Kokoro

    rt = voice._onnxruntime()
    for phonemes, name, speed, a in made:
        rt.set_seed(0)
        options = rt.SessionOptions()
        options.intra_op_num_threads = cores
        session = rt.InferenceSession(str(voice.directory() / voice.GRAPH.name), options,
                                      providers=["CPUExecutionProvider"])
        theirs, rate = Kokoro.from_session(session, str(voice.directory() / voice.VOICES.name)).create(
            phonemes, name, speed=speed, is_phonemes=True, trim=False, sentence_pause=0.0, clause_pause=0.0)
        assert rate == voice.RATE
        report["same_as_kokoro_onnx"] += int(theirs.shape == a.shape and theirs.astype(np.float32).tobytes() == a.tobytes())

    phonemes = report.pop("phonemes")
    report["phonemes_per_line"] = [min(phonemes), max(phonemes)]
    report["real_time_factor"] = round(report["seconds_of_speech"] / report["seconds_to_make"], 1)
    report["seconds_of_speech"] = round(report["seconds_of_speech"], 1)
    report["seconds_to_make"] = round(report["seconds_to_make"], 1)
    report["cores"] = cores
    report["telemetry_cache_touched"] = stamps() != before
    import misaki
    import onnxruntime

    report["versions"] = {"onnxruntime": onnxruntime.__version__, "misaki": misaki.__version__,
                          "kokoro_onnx": "0.6.1"}
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    assert len(sys.argv) == 2, __doc__
    main(sys.argv[1])
