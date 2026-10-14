"""What `lungecuta render` costs with more frames in flight, and that the result does not change.

Usage: uv run python bench/render_cost.py PROJECT COMP PRESET
  (inside the project)

Renders COMP with PRESET with 1, 4, 8 and 15 worker threads (OCIO single-threaded in each
when there are several, on all cores when there is one) and checks that every file's
decoded frames are identical (framemd5) to the one-worker render's.
"""
import subprocess
import sys
import time
from pathlib import Path

from lungecuta.media import Project
from lungecuta.render import render


def framemd5(path: Path) -> list[str]:
    lines = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [line.rsplit(",", 1)[-1].strip() for line in lines if line and not line.startswith("#")]


def main() -> None:
    project, comp, preset = Project.load(Path(sys.argv[1])), sys.argv[2], sys.argv[3]
    reference = None
    for workers in (1, 4, 8, 15):
        out = project.directory / "cache" / "lungecuta" / "render" / f"{comp}.bench{workers}.{preset}.mov"
        out = out.with_suffix(".mp4") if preset == "web-h264" else out
        start = time.perf_counter()
        result = render(project, comp, preset, out, workers=workers)
        took = time.perf_counter() - start
        hashes = framemd5(out)
        assert len(hashes) == result["frames"], "frames missing"
        reference = reference or hashes
        same = hashes == reference
        print(f"{comp} {preset}, {workers:2d} workers: {result['frames']} frames in {took:.1f} s, "
              f"{result['frames'] / took:.2f} fps, {result['seconds'] / took:.2f} x real time; "
              f"frames identical to 1 worker's: {same}")
        assert same, "the render depends on the number of workers"
        out.unlink()
        Path(str(out) + ".manifest.json").unlink()


if __name__ == "__main__":
    main()
