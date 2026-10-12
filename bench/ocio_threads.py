# /// script
# requires-python = ">=3.12"
# dependencies = ["opencolorio", "numpy"]
# ///
"""Can the ACES 2.0 output view run on the CPU? Thread scaling, and the inverse-view round trip.

Usage: uv run bench/ocio_threads.py

OCIO's CPU processor releases the GIL, so row bands on plain threads scale. The second
part sends a display-referred pattern through the inverse output view into the working
space and back, which is how display-referred layers enter the composite (04-color.md).
"""
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import PyOpenColorIO as ocio

cfg = ocio.Config.CreateFromBuiltinConfig("studio-config-v5.0.0_aces-v2.1_ocio-v2.6")
view = ocio.DisplayViewTransform()
view.setSrc("DaVinci Intermediate WideGamut")
view.setDisplay("Rec.1886 Rec.709 - Display")
view.setView("ACES 2.0 - SDR 100 nits (Rec.709)")
fwd = cfg.getProcessor(view).getDefaultCPUProcessor()
inv = cfg.getProcessor(view, ocio.TRANSFORM_DIR_INVERSE).getDefaultCPUProcessor()


def run(img: np.ndarray, threads: int) -> float:
    bands = [np.ascontiguousarray(b) for b in np.array_split(img, threads * 4, axis=0)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(threads) as ex:
        list(ex.map(fwd.applyRGB, bands))
    return (time.perf_counter() - t0) * 1000


rng = np.random.default_rng(0)
for h, w in ((2160, 3840), (540, 960)):
    img = rng.random((h, w, 3), dtype=np.float32)
    for n in (1, 5, 10, 15):
        print(f"ACES 2.0 view {w}x{h}, {n:2d} threads: {run(img, n):6.0f} ms")

g = np.linspace(0, 1, 1024, dtype=np.float32)
rg = np.stack(np.meshgrid(g, g, indexing="ij"), -1).reshape(-1, 2)
pattern = np.concatenate([rg, np.full((rg.shape[0], 1), 0.5, np.float32)], 1).reshape(1024, 1024, 3)
for blue in (0.0, 0.5, 1.0):
    pattern[..., 2] = blue
    x = np.ascontiguousarray(pattern)
    orig = x.copy()
    inv.applyRGB(x)
    fwd.applyRGB(x)
    e = np.abs(x - orig).max(-1) * 1023
    print(f"display -> inverse view -> view, B={blue}: 10-bit codes median {np.median(e):.4f}, "
          f"p99 {np.percentile(e, 99):.4f}, max {e.max():.3f}")
