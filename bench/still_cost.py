"""What one `lc still` costs, by stage and end to end.

Usage: uv run python bench/still_cost.py WORKDIR CLIP [FRAME]
  (inside the project, so that the lungecuta package is importable)

Imports CLIP into a project under WORKDIR (once; later runs reuse it), then times the
stages of `lc still --media KEY --at f:FRAME` in-process (seek and decode of the original,
input transform, the downscale for --height 540, output view, numbers, PNG encode) and
the whole command as a new process, at the clip's own size and at 540 lines. Each is run
three times and the median printed. Checked: every run gives the same frame, of the size
asked for, and the PNG decodes back (ffmpeg) to the frame's quantized codes.
"""
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from lungecuta import color
from lungecuta.decode import read_rgb
from lungecuta.geometry import area_resize, oriented, scaled_size, shown_size
from lungecuta.images import png
from lungecuta.media import Project, import_file, load_record, original, set_root
from lungecuta.project import init_project
from lungecuta.still import code_numbers
from lungecuta.ticks import parse_rate
from lungecuta.tools import run

RUNS = 3


def project_with(workdir: Path, clip: Path) -> tuple[Path, str]:
    directory = workdir / "still.lc"
    if not directory.exists():
        init_project(directory)
        set_root(directory, "footage", clip.parent)
        import_file(Project.load(directory), clip, root="footage")
    return directory, clip.stem


def stages(project: Project, key: str, index: int, height: int | None) -> tuple[dict[str, float], np.ndarray, bytes]:
    record = load_record(project, key)
    video, decision, settings = record.probe.video, record.color, project.file.color
    times = {}

    def timed(name, call):
        start = time.perf_counter()
        value = call()
        times[name] = (time.perf_counter() - start) * 1000
        return value

    path = original(project, record)
    frame = timed("decode", lambda: read_rgb(path, index, rate=parse_rate(video.rate), size=video.size,
                                             pix_fmt=video.pix_fmt, matrix=decision.matrix, range_=decision.range,
                                             chroma_location=video.chroma_location))
    into = color.cpu_processor(settings.config, decision.space, settings.working)
    view = color.view_processor(settings.config, settings.working, settings.display, settings.view)
    timed("input transform", lambda: color.apply(into, frame))
    frame = oriented(frame, video.rotation)
    if height is not None:
        frame = timed("downscale", lambda: area_resize(frame, scaled_size(shown_size(video.size, video.rotation), height)))
    timed("view", lambda: color.apply(view, frame))
    timed("numbers", lambda: code_numbers(frame))
    encoded = timed("png", lambda: png(frame))
    return times, frame, encoded


def main() -> None:
    workdir, clip = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    index = int(sys.argv[3]) if len(sys.argv) > 3 else 352
    workdir.mkdir(parents=True, exist_ok=True)
    directory, key = project_with(workdir, clip)
    project = Project.load(directory)
    print(f"{clip.name}, frame {index}, OCIO threads {os.cpu_count()}")
    for height in (None, 540):
        runs, frames = [], []
        for _ in range(RUNS):
            times, frame, encoded = stages(project, key, index, height)
            runs.append(times)
            frames.append(frame)
        assert all(np.array_equal(frames[0], other) for other in frames[1:]), "runs gave different frames"
        h, w = frames[0].shape[:2]
        assert height is None or h == height, f"asked for {height} lines, got {h}"
        out = workdir / "check.png"
        out.write_bytes(encoded)
        back = run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]).stdout
        eight = np.floor(np.clip(frames[0], 0, 1) * 255 + 0.5).astype(np.uint8)
        assert np.array_equal(np.frombuffer(back, np.uint8).reshape(h, w, 3), eight), "the PNG is not the frame"
        command = ["lc", "still", "--media", key, "--at", f"f:{index}", "--project", str(directory)]
        command += [] if height is None else ["--height", str(height)]
        walls = []
        for _ in range(RUNS):
            start = time.perf_counter()
            printed = json.loads(subprocess.run(command, capture_output=True, check=True).stdout)
            walls.append((time.perf_counter() - start) * 1000)
            assert printed["codes"] == code_numbers(frames[0]), "lc still printed other numbers"
        line = ", ".join(f"{name} {statistics.median(r[name] for r in runs):.0f}" for name in runs[0])
        print(f"  {w}x{h}: {line} ms; lc still as a process {statistics.median(walls):.0f} ms "
              f"(PNG {len(encoded) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
