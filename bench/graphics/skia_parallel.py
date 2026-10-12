"""Aggregate skia-python CPU raster throughput with P worker processes on disjoint frames.

Each worker renders into its own surface and reads back into its own numpy buffer; frames
are not gathered into one process, so this is an upper bound for a multi-process renderer.
The aggregate is the sum of per-worker rates (worker start-up excluded).

Usage: uv run --with skia-python==144.0.post2,numpy python bench/graphics/skia_parallel.py
"""
import multiprocessing as mp
import sys
import time
from pathlib import Path

import numpy as np

HERE = str(Path(__file__).resolve().parent)


def worker(args):
    W, H, frames, variant = args
    sys.path.insert(0, HERE)
    import skia
    from skia_bench import draw_scene, make_font, surface_info
    info, dtype = surface_info(W, H, variant)
    out = np.empty((H, W, 4), dtype)
    surface = skia.Surface.MakeRaster(info)
    assert surface is not None
    font = make_font(54 * W / 1080.0)
    draw_scene(surface.getCanvas(), W, H, 0, font)
    t0 = time.perf_counter()
    for n in frames:
        draw_scene(surface.getCanvas(), W, H, n, font)
        assert surface.readPixels(info, out, out.strides[0], 0, 0)
    return len(frames), time.perf_counter() - t0


if __name__ == "__main__":
    for (W, H, per) in [(1080, 1920, 120), (3840, 2160, 30)]:
        for variant in ("u8-srgb", "f16-linear-rec2020"):
            for P in (1, 4, 8):
                with mp.get_context("spawn").Pool(P) as pool:
                    res = pool.map(worker, [(W, H, list(range(i, per * P, P)), variant) for i in range(P)])
                agg = sum(n / dt for n, dt in res)
                print(f"skia-python {variant} {W}x{H} procs={P}: aggregate {agg:.0f} fps", flush=True)
