"""OpenColorIO, the colour reference (D2): processors from the built-in config, applied
on the CPU by row bands on threads, since the CPU processor releases the GIL; the output
view and its inverse, which are most of a frame's colour work, on the GPU (`metal.py`),
measured against the CPU as 04-color.md's faster paths are."""
from __future__ import annotations

import functools
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import PyOpenColorIO as ocio

from .camera_spaces import add_spaces
from .metal import GpuProcessor, supported

# The space proxy parity is measured in (docs/design/02-media.md#proxies).
DAVINCI_INTERMEDIATE = "DaVinci Intermediate WideGamut"


@functools.cache
def config(name: str) -> ocio.Config:
    """The built-in config `name`, with the camera spaces lungecuta adds to it
    (`camera_spaces.py`: DJI D-Log2 D-Gamut2, from DJI's published transform)."""
    cfg = ocio.Config.CreateFromBuiltinConfig(name)
    add_spaces(cfg)
    return cfg


@functools.cache
def cpu_processor(config_name: str, source: str, target: str) -> ocio.CPUProcessor:
    cfg = config(config_name)
    for space in (source, target):
        assert cfg.getColorSpace(space) is not None, f"{space!r} is not a colour space of {config_name}"
    return cfg.getProcessor(source, target).getDefaultCPUProcessor()


def _on_gpu(processor: ocio.Processor) -> GpuProcessor | ocio.CPUProcessor:
    """The output view on the GPU where `metal.py` runs it (every SDR display of the
    built-in config); the HDR displays' curves stay on the CPU."""
    if supported(processor):
        return GpuProcessor(processor, processor.getDefaultCPUProcessor())
    return processor.getDefaultCPUProcessor()


@functools.cache
def view_processor(config_name: str, source: str, display: str, view: str) -> GpuProcessor | ocio.CPUProcessor:
    """The output transform: from `source` to the display-encoded values of a display and
    view, on the GPU where it can run there (`_on_gpu`)."""
    cfg = config(config_name)
    assert cfg.getColorSpace(source) is not None, f"{source!r} is not a colour space of {config_name}"
    assert display in cfg.getDisplays(), f"{display!r} is not a display of {config_name}"
    assert view in cfg.getViews(display), f"{view!r} is not a view of {display!r} in {config_name}"
    transform = ocio.DisplayViewTransform()
    transform.setSrc(source)
    transform.setDisplay(display)
    transform.setView(view)
    return _on_gpu(cfg.getProcessor(transform))


@functools.cache
def inverse_view_processor(config_name: str, target: str, display: str, view: str) -> GpuProcessor | ocio.CPUProcessor:
    """The output transform run backwards: from display-encoded values of a display and view
    to `target`, how a display-referred layer under footage enters the working space; on
    the GPU where it can run there (`_on_gpu`)."""
    cfg = config(config_name)
    transform = ocio.DisplayViewTransform()
    transform.setSrc(target)
    transform.setDisplay(display)
    transform.setView(view)
    transform.setDirection(ocio.TRANSFORM_DIR_INVERSE)
    return _on_gpu(cfg.getProcessor(transform))


# A clip decided as display-referred video (`lungecuta decide KEY --colour display`): its codes
# are the deliverable's, so it enters the working space through the inverse of the output
# view, as a graphics layer in display space does under footage (01-architecture.md, Where
# display-referred layers enter). Not an OCIO colour space of the config.
DISPLAY = "display"


def input_processor(settings, space: str, target: str | None = None) -> ocio.CPUProcessor | GpuProcessor:
    """A clip's input transform, from its decided `space` into `target` (the project's
    working space by default): the colour space's own conversion, or for display-referred
    video the project's output view run backwards. `settings` is the project's colour
    settings (`ColorSettings`)."""
    target = target or settings.working
    if space == DISPLAY:
        return inverse_view_processor(settings.config, target, settings.display, settings.view)
    return cpu_processor(settings.config, space, target)


def apply(processor: ocio.CPUProcessor | GpuProcessor, image: np.ndarray, threads: int | None = None) -> None:
    """Transform a float32 HxWx3 image in place: on the GPU at once for a `GpuProcessor`
    (`threads` unused), otherwise by row bands on `threads` CPU threads."""
    assert image.dtype == np.float32 and image.ndim == 3 and image.shape[2] == 3, "expected float32 HxWx3"
    assert image.flags.c_contiguous, "the image must be C-contiguous to be transformed in place"
    if isinstance(processor, GpuProcessor):
        processor.apply(image)
        return
    threads = threads or os.cpu_count() or 1
    # Contiguous row views of `image`; a small image gives some empty ones, left out.
    bands = [band for band in np.array_split(image, threads * 4, axis=0) if band.size]
    with ThreadPoolExecutor(threads) as pool:
        list(pool.map(processor.applyRGB, bands))
