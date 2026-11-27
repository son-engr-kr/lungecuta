"""OCIO's output views on the GPU (`src/lungecuta/metal.py`) against OCIO's CPU processor,
on the dense pattern of 04-color.md's faster paths, and their speed.

Usage: uv run python bench/ocio_gpu.py

For every display and view of the built-in config that `metal.py` runs, forwards from
DaVinci Intermediate and backwards into it: a 129^3 grid of the unit cube plus 2,000,000
uniform points (forwards over [-0.05, 1.05] of DaVinci Intermediate, where graded and
blended values reach past the cube; backwards over [0, 1] of the display's codes). Each is
compared in ten-bit codes of the output with three CPU processors of the same transform:
OCIO's default (`getDefaultCPUProcessor`, what lungecuta used before), the default without
its fast log, exp and pow approximations, and OPTIMIZATION_LOSSLESS. Reported per
transform: the largest difference, its 99.99th percentile and the points over 1 code; then
whether the GPU gives the same bytes on a second run, and the ms a 1920x1080 frame takes
(the copies to and from the GPU included) against the default CPU processor on one thread.
"""
import time

import numpy as np
import PyOpenColorIO as ocio

from lungecuta import color
from lungecuta.metal import GpuProcessor, supported

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"
SOURCE = color.DAVINCI_INTERMEDIATE
FLAGS = ocio.OptimizationFlags
NO_FAST = FLAGS(int(FLAGS.OPTIMIZATION_DEFAULT) & ~int(FLAGS.OPTIMIZATION_FAST_LOG_EXP_POW))


def pattern(low: float, high: float) -> np.ndarray:
    grid = np.linspace(0, 1, 129, dtype=np.float32)
    cube = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), -1).reshape(-1, 3)
    rng = np.random.default_rng(0)
    return np.concatenate([cube, rng.uniform(low, high, (2_000_000, 3)).astype(np.float32)])


def cpu(processor: ocio.Processor, flags, points: np.ndarray) -> np.ndarray:
    out = points.copy()
    processor.getOptimizedCPUProcessor(flags).applyRGB(out)
    return out


def codes(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    finite = np.isfinite(a).all(1) & np.isfinite(b).all(1)
    assert finite.all(), f"{(~finite).sum()} points not finite"
    return (np.abs(a - b) * 1023).max(1)


def row(name: str, error: np.ndarray) -> str:
    return f"{name}: max {error.max():.3f}, p99.99 {np.percentile(error, 99.99):.4f}, over 1 code {(error > 1).sum()}"


def main() -> None:
    cfg = color.config(CONFIG)
    forwards, backwards = pattern(-0.05, 1.05), pattern(0.0, 1.0)
    frame = np.random.default_rng(1).uniform(0, 1, (1080, 1920, 3)).astype(np.float32)
    print(f"{forwards.shape[0]} points a transform")
    for display in cfg.getDisplays():
        for view in cfg.getViews(display):
            for direction, points in ((ocio.TRANSFORM_DIR_FORWARD, forwards), (ocio.TRANSFORM_DIR_INVERSE, backwards)):
                transform = ocio.DisplayViewTransform()
                transform.setSrc(SOURCE)
                transform.setDisplay(display)
                transform.setView(view)
                transform.setDirection(direction)
                processor = cfg.getProcessor(transform)
                if not supported(processor):
                    continue
                gpu = GpuProcessor(processor, processor.getDefaultCPUProcessor())
                ours = points.copy()
                gpu.apply(ours)
                again = points.copy()
                gpu.apply(again)
                exact = cpu(processor, FLAGS.OPTIMIZATION_LOSSLESS, points)
                default = cpu(processor, FLAGS.OPTIMIZATION_DEFAULT, points)
                arrow = "->" if direction == ocio.TRANSFORM_DIR_FORWARD else "<-"
                print(f"\n{SOURCE} {arrow} {display} / {view}")
                print("  " + row("gpu against cpu default", codes(ours, default)))
                print("  " + row("gpu against cpu without fast pow", codes(ours, cpu(processor, NO_FAST, points))))
                print("  " + row("gpu against cpu lossless", codes(ours, exact)))
                print("  " + row("cpu default against cpu lossless", codes(default, exact)))
                print(f"  gpu twice the same bytes: {ours.tobytes() == again.tobytes()}")
                shown = frame.copy()
                gpu.apply(shown)
                begun = time.perf_counter()
                for _ in range(10):
                    shown = frame.copy()
                    gpu.apply(shown)
                on_gpu = (time.perf_counter() - begun) * 100
                shown = frame.copy()
                begun = time.perf_counter()
                gpu.cpu.applyRGB(shown)
                on_cpu = (time.perf_counter() - begun) * 1000
                print(f"  1920x1080: gpu {on_gpu:.1f} ms, cpu default on one thread {on_cpu:.1f} ms")


if __name__ == "__main__":
    main()
