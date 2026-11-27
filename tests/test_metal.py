"""OCIO's output view on the GPU (`metal.py`) against OCIO's CPU processor, on a smaller
version of 04-color.md's dense pattern (`bench/ocio_gpu.py` measures the full one)."""
import numpy as np
import PyOpenColorIO as ocio

from lungecuta import color
from lungecuta.metal import GpuProcessor, supported

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"
DISPLAY = "Rec.1886 Rec.709 - Display"
VIEW = "ACES 2.0 - SDR 100 nits (Rec.709)"


def _pattern(low: float, high: float) -> np.ndarray:
    grid = np.linspace(0, 1, 33, dtype=np.float32)
    cube = np.stack(np.meshgrid(grid, grid, grid, indexing="ij"), -1).reshape(-1, 3)
    rng = np.random.default_rng(0)
    return np.concatenate([cube, rng.uniform(low, high, (300_000, 3)).astype(np.float32)])


def _over_a_code(a: np.ndarray, b: np.ndarray) -> tuple[int, float]:
    error = (np.abs(a - b) * 1023).max(1)
    return int((error > 1).sum()), float(error.max())


def _check(processor, points: np.ndarray) -> None:
    assert isinstance(processor, GpuProcessor)
    ours = points.copy()
    processor.apply(ours)
    again = points.copy()
    processor.apply(again)
    assert ours.tobytes() == again.tobytes()
    exact = points.copy()
    processor.processor.getOptimizedCPUProcessor(ocio.OptimizationFlags.OPTIMIZATION_LOSSLESS).applyRGB(exact)
    default = points.copy()
    processor.cpu.applyRGB(default)
    gpu_over, gpu_max = _over_a_code(ours, exact)
    cpu_over, cpu_max = _over_a_code(default, exact)
    # 04-color.md: no more points over a code from OCIO's exact processor than OCIO's default has
    assert gpu_over <= cpu_over, (gpu_over, gpu_max, cpu_over, cpu_max)
    assert gpu_max <= max(1.0, cpu_max), (gpu_max, cpu_max)


def test_output_view_on_the_gpu_is_as_close_as_ocio_cpu():
    _check(color.view_processor(CONFIG, color.DAVINCI_INTERMEDIATE, DISPLAY, VIEW), _pattern(-0.05, 1.05))


def test_inverse_view_on_the_gpu_is_as_close_as_ocio_cpu():
    _check(color.inverse_view_processor(CONFIG, color.DAVINCI_INTERMEDIATE, DISPLAY, VIEW), _pattern(0.0, 1.0))


def test_apply_takes_a_frame_whole():
    processor = color.view_processor(CONFIG, color.DAVINCI_INTERMEDIATE, DISPLAY, VIEW)
    frame = np.random.default_rng(2).uniform(0, 1, (37, 53, 3)).astype(np.float32)
    flat = frame.reshape(-1, 3).copy()
    processor.apply(flat)
    color.apply(processor, frame)
    assert frame.reshape(-1, 3).tobytes() == flat.tobytes()
    color.apply(processor, np.zeros((0, 4, 3), np.float32))


def test_hdr_curves_stay_on_the_cpu():
    cfg = color.config(CONFIG)
    transform = ocio.DisplayViewTransform()
    transform.setSrc(color.DAVINCI_INTERMEDIATE)
    transform.setDisplay("Rec.2100-PQ - Display")
    transform.setView("ACES 2.0 - HDR 1000 nits (P3 D65)")
    assert not supported(cfg.getProcessor(transform))
    processor = color.view_processor(CONFIG, color.DAVINCI_INTERMEDIATE, "Rec.2100-PQ - Display",
                                     "ACES 2.0 - HDR 1000 nits (P3 D65)")
    assert isinstance(processor, ocio.CPUProcessor)
