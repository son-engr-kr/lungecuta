# /// script
# requires-python = ">=3.12"
# dependencies = ["opencolorio", "numpy"]
# ///
"""What the bundled OpenColorIO studio config covers, and what its transforms cost on the CPU.

Usage: uv run bench/ocio_check.py

Prints the camera and display colour spaces of the built-in studio config, the views
offered for a Rec.1886 display, CPU time for an input and an output transform on a
4K float32 frame, and whether a Metal (MSL) shader can be generated for each view.
"""
import time

import numpy as np
import PyOpenColorIO as ocio

CONFIG = "studio-config-v5.0.0_aces-v2.1_ocio-v2.6"
DISPLAY = "Rec.1886 Rec.709 - Display"
WORKING = "DaVinci Intermediate WideGamut"
KEYS = ("S-Log", "D-Log", "DJI", "DaVinci", "Apple", "BMD", "Rec.709", "Rec.1886", "sRGB", "2100")

print("OCIO", ocio.__version__)
cfg = ocio.Config.CreateFromBuiltinConfig(CONFIG)
print("config:", CONFIG)
print("colour spaces:", [c.getName() for c in cfg.getColorSpaces() if any(k in c.getName() for k in KEYS)])
views = list(cfg.getViews(DISPLAY))
print(f"views for {DISPLAY}:", views)

frame = np.random.default_rng(0).random((2160, 3840, 3), dtype=np.float32)

cpu = cfg.getProcessor("S-Log3 S-Gamut3.Cine", WORKING).getDefaultCPUProcessor()
img = frame.copy()
t0 = time.perf_counter()
cpu.applyRGB(img)
print(f"CPU S-Log3 S-Gamut3.Cine -> {WORKING}, 4K: {(time.perf_counter() - t0) * 1000:.0f} ms")

for view in views:
    t = ocio.DisplayViewTransform()
    t.setSrc(WORKING)
    t.setDisplay(DISPLAY)
    t.setView(view)
    proc = cfg.getProcessor(t)
    img = frame.copy()
    t0 = time.perf_counter()
    proc.getDefaultCPUProcessor().applyRGB(img)
    cpu_ms = (time.perf_counter() - t0) * 1000
    desc = ocio.GpuShaderDesc.CreateShaderDesc(language=ocio.GPU_LANGUAGE_MSL_2_0)
    proc.getDefaultGPUProcessor().extractGpuShaderInfo(desc)
    print(f"view {view!r}: CPU 4K {cpu_ms:.0f} ms | MSL {len(desc.getShaderText())} chars, "
          f"{len(list(desc.getTextures()))} 1D/2D textures, {len(list(desc.get3DTextures()))} 3D textures")
    grey = np.full((1, 1, 3), 0.336043, np.float32)  # DaVinci Intermediate code of 18 % grey
    proc.getDefaultCPUProcessor().applyRGB(grey)
    print(f"  DI 18 % grey (0.336043) -> display code {grey[0, 0, 1]:.4f}")
