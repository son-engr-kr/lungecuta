"""Two ffmpeg builds against each other: whether they decode the same files to the same
frames and samples.

Usage: uv run python bench/ffmpeg_builds.py DIR_A DIR_B FILE...
  DIR_A and DIR_B hold an `ffmpeg` each (the Homebrew build's bin directory, the pinned
  build's directory under lungecuta's cache); stdlib only.

For each FILE, both builds write the framemd5 of every video frame (the decoder's own
pixel format, `-noautorotate`, as lungecuta decodes) and of every audio frame (decoded to
32-bit float, as lungecuta reads music), and the lines are compared. A build that decodes
differently changes what every measurement made with the other one stands for, so the
check is that all lines match. Prints JSON: per file, the frames compared, the first
differing line, if any, and for audio that differs the largest difference of a sample
(full scale is 1).
"""
import array
import json
import subprocess
import sys
from pathlib import Path


def framemd5(ffmpeg: Path, file: Path, stream: str) -> list[str]:
    output = ["-c:a", "pcm_f32le"] if stream == "a" else []
    done = subprocess.run([str(ffmpeg), "-v", "error", "-noautorotate", "-i", str(file), "-map", f"0:{stream}:0",
                           *output, "-f", "framemd5", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    return [line for line in done.stdout.splitlines() if not line.startswith("#")]


def samples(ffmpeg: Path, file: Path) -> array.array:
    done = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(file), "-map", "0:a:0", "-f", "f32le",
                           "-c:a", "pcm_f32le", "-"], capture_output=True, stdin=subprocess.DEVNULL)
    assert done.returncode == 0, done.stderr
    return array.array("f", done.stdout)


def has_stream(ffmpeg: Path, file: Path, stream: str) -> bool:
    done = subprocess.run([str(ffmpeg), "-v", "error", "-i", str(file), "-map", f"0:{stream}:0", "-frames", "1",
                           "-f", "null", "-"], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return done.returncode == 0


def version(ffmpeg: Path) -> str:
    return subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True, text=True).stdout.splitlines()[0]


def main() -> None:
    a, b = Path(sys.argv[1]) / "ffmpeg", Path(sys.argv[2]) / "ffmpeg"
    files = [Path(f) for f in sys.argv[3:]]
    assert files, __doc__
    out = {"a": version(a), "b": version(b), "files": []}
    for file in files:
        entry = {"file": file.name}
        for stream in ("v", "a"):
            if not has_stream(a, file, stream):
                continue
            lines_a, lines_b = framemd5(a, file, stream), framemd5(b, file, stream)
            differing = next((i for i, (x, y) in enumerate(zip(lines_a, lines_b)) if x != y), None)
            entry[stream] = {"frames": [len(lines_a), len(lines_b)], "identical": lines_a == lines_b,
                             "first_difference": differing}
            if stream == "a" and lines_a != lines_b:
                x, y = samples(a, file), samples(b, file)
                assert len(x) == len(y), (len(x), len(y))
                entry[stream] |= {"samples": len(x), "samples_differing": sum(p != q for p, q in zip(x, y)),
                                  "largest_difference": max(abs(p - q) for p, q in zip(x, y))}
        out["files"].append(entry)
    print(json.dumps(out, indent=2))


main()
