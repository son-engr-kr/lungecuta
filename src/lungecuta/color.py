"""OpenColorIO, the colour reference (D2): processors from the built-in config, applied
on the CPU by row bands on threads, since the CPU processor releases the GIL."""
from __future__ import annotations

import functools
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import PyOpenColorIO as ocio

# The space proxy parity is measured in (docs/design/02-media.md#proxies).
DAVINCI_INTERMEDIATE = "DaVinci Intermediate WideGamut"


@functools.cache
def config(name: str) -> ocio.Config:
    return ocio.Config.CreateFromBuiltinConfig(name)


@functools.cache
def cpu_processor(config_name: str, source: str, target: str) -> ocio.CPUProcessor:
    cfg = config(config_name)
    for space in (source, target):
        assert cfg.getColorSpace(space) is not None, f"{space!r} is not a colour space of {config_name}"
    return cfg.getProcessor(source, target).getDefaultCPUProcessor()


@functools.cache
def view_processor(config_name: str, source: str, display: str, view: str) -> ocio.CPUProcessor:
    """The output transform: from `source` to the display-encoded values of a display and view."""
    cfg = config(config_name)
    assert cfg.getColorSpace(source) is not None, f"{source!r} is not a colour space of {config_name}"
    assert display in cfg.getDisplays(), f"{display!r} is not a display of {config_name}"
    assert view in cfg.getViews(display), f"{view!r} is not a view of {display!r} in {config_name}"
    transform = ocio.DisplayViewTransform()
    transform.setSrc(source)
    transform.setDisplay(display)
    transform.setView(view)
    return cfg.getProcessor(transform).getDefaultCPUProcessor()


@functools.cache
def inverse_view_processor(config_name: str, target: str, display: str, view: str) -> ocio.CPUProcessor:
    """The output transform run backwards: from display-encoded values of a display and view
    to `target`, how a display-referred layer under footage enters the working space."""
    cfg = config(config_name)
    transform = ocio.DisplayViewTransform()
    transform.setSrc(target)
    transform.setDisplay(display)
    transform.setView(view)
    transform.setDirection(ocio.TRANSFORM_DIR_INVERSE)
    return cfg.getProcessor(transform).getDefaultCPUProcessor()


def apply(processor: ocio.CPUProcessor, image: np.ndarray, threads: int | None = None) -> None:
    """Transform a float32 HxWx3 image in place."""
    assert image.dtype == np.float32 and image.ndim == 3 and image.shape[2] == 3, "expected float32 HxWx3"
    assert image.flags.c_contiguous, "the image must be C-contiguous to be transformed in place"
    threads = threads or os.cpu_count() or 1
    # Contiguous row views of `image`; a small image gives some empty ones, left out.
    bands = [band for band in np.array_split(image, threads * 4, axis=0) if band.size]
    with ThreadPoolExecutor(threads) as pool:
        list(pool.map(processor.applyRGB, bands))
