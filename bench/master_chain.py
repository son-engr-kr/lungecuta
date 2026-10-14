"""The master chain on real music, and what the delivery codecs do to its loudness and peaks.

Usage: uv run python bench/master_chain.py WAV [TARGET_LUFS...]
  (inside the project, so that the lungecuta package is importable; WAV is a 48 kHz music
  WAV such as the cache WAV `lungecuta import` writes)

For each target (by default -14 and -9 LUFS), the WAV goes through `audio.master` at a
ceiling of -1 dBTP, then:
1. the engine's own numbers: loudness by pyloudnorm and true peak by 4x soxr oversampling,
   before and after, the gain and the deepest limiter cut, and the time taken;
2. the same numbers by ffmpeg's `ebur128` filter (BS.1770, `peak=true`) on the mastered
   WAV, so the two meters are compared on identical samples;
3. the mastered WAV encoded as each preset encodes audio (24-bit PCM; AAC at 256 kbps by
   AudioToolbox), decoded, and measured by `ebur128` again. Before a number is quoted the
   decode is checked: PCM gives back exactly the samples; AAC starts at lag 0 (its priming
   trimmed by ffmpeg), and the samples it has past the input's end (the last AAC frame's
   padding) are counted with their peak level.
"""
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from lungecuta import audio
from lungecuta.music import read_wav, wav_bytes


def ebur128(path: Path) -> dict[str, float]:
    text = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true",
                           "-f", "null", "-"], capture_output=True, check=True).stderr.decode()
    summary = text[text.rindex("Summary:"):]
    return {"lufs": float(re.search(r"I:\s+(-?[\d.]+) LUFS", summary)[1]),
            "dbtp": float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", summary)[1])}


def decoded(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "2", "-ar", "48000", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, 2)


def check_decode(name: str, out: np.ndarray, back: np.ndarray) -> dict:
    """`back` checked to start with `out`; the samples it has past `out`'s end."""
    if name.startswith("pcm"):
        assert back.shape == out.shape, f"{name}: {len(back)} samples decoded for {len(out)}"
        return {}
    assert len(back) >= len(out), f"{name}: {len(back)} samples decoded for {len(out)}"
    piece = out[48000:48000 * 3, 0]
    lag = max(range(-3000, 3001), key=lambda k: float(np.dot(piece, back[48000 + k:48000 + k + piece.size, 0])))
    assert lag == 0, f"{name}: decoded {lag} samples late"
    extra = back[len(out):]
    top = float(np.abs(extra).max()) if extra.size else 0.0
    return {"samples_past_the_end": len(extra), "their_peak_dbfs": round(20 * np.log10(top), 1) if top else None}


def main() -> None:
    path = Path(sys.argv[1])
    targets = [float(t) for t in sys.argv[2:]] or [-14.0, -9.0]
    signal, rate = read_wav(path)
    assert rate == 48000, f"{path.name} is {rate} Hz"
    print(json.dumps({"input": {"seconds": round(len(signal) / rate, 2), "lufs": round(audio.loudness(signal, rate), 2),
                                "dbtp": round(audio.true_peak_db(signal, rate), 2)}}))
    with tempfile.TemporaryDirectory() as directory:
        for target in targets:
            start = time.perf_counter()
            out, report = audio.master(signal, rate, target_lufs=target, ceiling_dbtp=-1.0)
            seconds = time.perf_counter() - start
            wav = Path(directory) / f"master{target}.wav"
            wav.write_bytes(wav_bytes(out, rate))
            row = {"target": target, "engine": report, "seconds": round(seconds, 2), "ebur128_wav": ebur128(wav)}
            for name, codec in (("pcm_s24le", ["-c:a", "pcm_s24le"]), ("aac_at_256k", ["-c:a", "aac_at", "-b:a", "256k"])):
                encoded = Path(directory) / f"master{target}.{name}.{'mov' if name.startswith('pcm') else 'm4a'}"
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), *codec, str(encoded)], check=True)
                extra = check_decode(name, out, decoded(encoded))
                row[name] = ebur128(encoded) | extra
            print(json.dumps(row))


if __name__ == "__main__":
    main()
