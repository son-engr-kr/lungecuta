"""What `lungecuta sheet` and `lungecuta scope` cost, and one decode against a seek per frame.

Usage: uv run python bench/sheet_scope_cost.py WORKDIR CLIP
  (inside the project, so that the lungecuta package is importable)

Imports CLIP into a project under WORKDIR (once; later runs reuse it). Then:
- the frames of a sheet at every 30th frame read two ways, one sequential decode
  (`read_every`) and one seek per frame (`read_rgb`); the two must give identical frames;
- `lungecuta sheet --every f:30` and `f:5`, and `lungecuta scope --at f:352` at the clip's size and at
  540 lines, each as a new process, median of three runs, with the scope's clipped
  fractions and how many pixels the view puts at exactly 0. Checked: the sheet has one
  tile per interval, and the scope prints the numbers of the same frame rendered in-process.
"""
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

from lungecuta.decode import read_every, read_rgb
from lungecuta.media import Project, import_file, load_record, set_root
from lungecuta.project import init_project
from lungecuta.scope import scope_numbers
from lungecuta.still import open_clip, render

RUNS = 3


def project_with(workdir: Path, clip: Path) -> tuple[Path, str]:
    directory = workdir / "still.lungecuta"
    if not directory.exists():
        init_project(directory)
        set_root(directory, "footage", clip.parent)
        import_file(Project.load(directory), clip, root="footage")
    return directory, clip.stem


def timed(call) -> tuple[float, object]:
    start = time.perf_counter()
    value = call()
    return (time.perf_counter() - start) * 1000, value


def process(command: list[str]) -> tuple[float, dict]:
    walls, printed = [], None
    for _ in range(RUNS):
        wall, done = timed(lambda: subprocess.run(command, capture_output=True, check=True))
        walls.append(wall)
        printed = json.loads(done.stdout)
    return statistics.median(walls), printed


def main() -> None:
    workdir, clip_path = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    directory, key = project_with(workdir, clip_path)
    project = Project.load(directory)
    record = load_record(project, key)
    clip = open_clip(project, record)
    video = clip.video
    print(f"{clip_path.name}: {video.size[0]}x{video.size[1]}, {video.frames} frames")

    indices = list(range(0, video.frames, 30))
    common = dict(size=video.size, pix_fmt=video.pix_fmt, matrix=clip.decision.matrix, range_=clip.decision.range,
                  chroma_location=video.chroma_location)
    one, sequential = timed(lambda: list(read_every(clip.path, 30, len(indices), **common)))
    each, seeked = timed(lambda: [read_rgb(clip.path, i, rate=clip.rate, **common) for i in indices])
    assert all(np.array_equal(a, b) for a, b in zip(sequential, seeked, strict=True)), "the two reads differ"
    del sequential, seeked
    print(f"  {len(indices)} frames at every 30th: one decode {one:.0f} ms, a seek per frame {each:.0f} ms (identical)")

    for step in (30, 5):
        wall, printed = process(["lungecuta", "sheet", "--media", key, "--every", f"f:{step}",
                                 "--project", str(directory)])
        assert [tile["frame"] for tile in printed["tiles"]] == list(range(0, video.frames, step)), "tiles missing"
        print(f"  lungecuta sheet --every f:{step}: {len(printed['tiles'])} tiles on {len(printed['pages'])} pages, "
              f"{wall:.0f} ms")

    for height in (None, 540):
        command = ["lungecuta", "scope", "--media", key, "--at", "f:352", "--project", str(directory)]
        command += [] if height is None else ["--height", str(height)]
        wall, printed = process(command)
        frame = render(project, record, 352, height=height)
        expected = scope_numbers(frame)
        assert {name: printed[name] for name in expected} == expected, "lungecuta scope printed other numbers"
        print(f"  lungecuta scope at {printed['size'][0]}x{printed['size'][1]}: {wall:.0f} ms; "
              f"clipped {printed['clipped']}, min {frame.min():.6f}, a channel exactly 0 in {(frame == 0).any(axis=-1).mean():.4f} of pixels")


if __name__ == "__main__":
    main()
