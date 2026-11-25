"""Where a render's time goes once its graphics are captured, and how it scales with
workers: the comp's first COUNT frames made as `lungecuta render` makes them.

Usage: uv run python bench/render_scaling.py PROJECT COMP COUNT PRESET WORKERS...

The graphics captures are made first (or found in the cache). Then:
- the inputs of every frame are read on one thread, as the render's own thread reads them
  (footage from ffmpeg, captures from the cache), and timed;
- the frames are made from those inputs with each count of WORKERS (one OCIO thread a
  frame, as the render runs), timed, and every frame's bytes hashed and compared with the
  first count's, so a speed is only quoted for identical output;
- the time spent inside OpenColorIO (`color.apply`) is summed over every thread.
Prints ms a frame for each.
"""
import hashlib
import sys
import threading
import time
from pathlib import Path

from lungecuta import color, compose, overlays
from lungecuta.comp import load_comp, resolve
from lungecuta.encode import PRESETS, frame_bytes
from lungecuta.media import Project
from lungecuta.parallel import in_order


def main() -> None:
    project = Project.load(Path(sys.argv[1]))
    resolved = resolve(project, load_comp(project, sys.argv[2]))
    count = int(sys.argv[3])
    preset = PRESETS[sys.argv[4]]
    counts = [int(value) for value in sys.argv[5:]]
    indices = range(min(count, resolved.end))
    start = time.perf_counter()
    entries = overlays.captures(project, resolved, indices)
    print(f"captures: {time.perf_counter() - start:.1f} s")

    start = time.perf_counter()
    jobs = []
    for index, raws in zip(indices, compose.raw_layers(project, resolved)):
        jobs.append((index, raws, compose.captured(resolved, index, entries)))
    read = time.perf_counter() - start
    print(f"{len(jobs)} frames; inputs read on one thread: {1000 * read / len(jobs):.1f} ms a frame")

    in_ocio = {"seconds": 0.0}
    lock = threading.Lock()
    apply = color.apply

    def timed_apply(*args, **kwargs):
        begun = time.perf_counter()
        result = apply(*args, **kwargs)
        with lock:
            in_ocio["seconds"] += time.perf_counter() - begun
        return result

    color.apply = timed_apply

    def job(index, raws, captures):
        frame = compose.compose(project, resolved, index, raws, captures, threads=1, alpha=preset.alpha)
        return hashlib.sha256(frame_bytes(frame, preset, seed=index)[0]).hexdigest()

    reference = None
    for workers in counts:
        in_ocio["seconds"] = 0.0
        start = time.perf_counter()
        digests = list(in_order(job, iter(jobs), workers=workers, ahead=2 * workers))
        spent = time.perf_counter() - start
        if reference is None:
            reference = digests
        assert digests == reference, f"{workers} workers made other frames than {counts[0]}"
        print(f"{workers} workers: {1000 * spent / len(jobs):.1f} ms a frame, wall; "
              f"OCIO {1000 * in_ocio['seconds'] / len(jobs):.1f} ms a frame, summed over threads; identical")


if __name__ == "__main__":
    main()
