"""Phase 1 spike: cache refill after a timeline-wide change.

Usage: uv run --with aiohttp,av python bench/spike/refill.py PROJECT KEY
  (inside the project)

A 60 s comp at the clip's rate (1798 frames at 29.97) cut from its 540-line proxy, played
through from the start, each frame read by PyAV in order, through the input transform, a
grade (the timeline-wide change), the output view and sRGB for the viewer, and stored as
8-bit RGB in memory. Two ways: one frame at a time with OCIO on every core, and frames in
parallel (one decoding thread feeding several render threads, OCIO single-threaded in
each). Checked: every frame of the comp arrives once, and frames rendered both ways are
identical. Criterion (docs/design/10-roadmap.md, Phase 1): at least real time (*proposed*).
"""
import os
import queue
import sys
import threading
import time
from pathlib import Path

import av
import numpy as np
import PyOpenColorIO as ocio

sys.path.insert(0, str(Path(__file__).resolve().parent))

from engine import Engine  # noqa: E402

from lungecuta import color  # noqa: E402
from lungecuta.decode import to_rgb  # noqa: E402

SECONDS = 60


def frames_of(engine: Engine, count: int):
    """The proxy's frames in order, looped, as (comp index, planes)."""
    produced = 0
    while produced < count:
        with av.open(str(engine.proxy_path)) as container:
            for frame in container.decode(video=0):
                planes = np.stack([np.frombuffer(p, np.uint16).reshape(p.height, p.line_size // 2)[:, :p.width]
                                   for p in frame.planes]).astype(np.float32)
                yield produced, planes
                produced += 1
                if produced == count:
                    return


def render(engine: Engine, planes: np.ndarray, threads: int) -> np.ndarray:
    frame = to_rgb(*planes, bits=10, matrix=engine.decision.matrix, range_=engine.decision.range)
    color.apply(engine.into, frame, threads)
    color.apply(engine.grade, frame, threads)
    color.apply(engine.view, frame, threads)
    color.apply(engine.to_viewer, frame, threads)
    return np.floor(np.clip(frame, 0, 1) * np.float32(255) + np.float32(0.5)).astype(np.uint8)


def one_at_a_time(engine: Engine, count: int) -> tuple[float, dict]:
    cache, start = {}, time.perf_counter()
    for index, planes in frames_of(engine, count):
        cache[index] = render(engine, planes, os.cpu_count())
    return time.perf_counter() - start, cache


def in_parallel(engine: Engine, count: int, workers: int) -> tuple[float, dict]:
    cache, lock = {}, threading.Lock()
    work: queue.Queue = queue.Queue(maxsize=workers * 2)

    def worker():
        while (item := work.get()) is not None:
            index, planes = item
            image = render(engine, planes, 1)
            with lock:
                assert index not in cache
                cache[index] = image

    start = time.perf_counter()
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for item in frames_of(engine, count):
        work.put(item)
    for _ in threads:
        work.put(None)
    for thread in threads:
        thread.join()
    return time.perf_counter() - start, cache


def main() -> None:
    project, key = Path(sys.argv[1]), sys.argv[2]
    engine = Engine(project, key)
    values = ocio.GradingPrimary(ocio.GRADING_LOG)
    values.exposure = ocio.GradingRGBM(0, 0, 0, 0.3)
    values.saturation = 1.2
    engine.grade_values.setGradingPrimary(values)
    count = int(SECONDS * engine.proxy.rate)
    print(f"{key} proxy {engine.size[0]}x{engine.size[1]}, a {SECONDS} s comp of {count} frames, {os.cpu_count()} cores")
    serial, reference = one_at_a_time(engine, count)
    assert sorted(reference) == list(range(count)), "frames missing"
    print(f"  one frame at a time, OCIO on {os.cpu_count()} threads: {serial:.1f} s, {count / serial:.1f} fps, "
          f"{SECONDS / serial:.2f} x real time")
    for workers in (4, 8, 15):
        took, cache = in_parallel(engine, count, workers)
        assert sorted(cache) == list(range(count)), "frames missing"
        assert all(np.array_equal(cache[k], reference[k]) for k in range(0, count, 97)), "the two ways differ"
        print(f"  {workers} frames in parallel, OCIO single-threaded each: {took:.1f} s, {count / took:.1f} fps, "
              f"{SECONDS / took:.2f} x real time")
    size = sum(image.nbytes for image in reference.values())
    print(f"  the cache as 8-bit RGB in memory: {size / 1e9:.2f} GB for {SECONDS} s")


if __name__ == "__main__":
    main()
