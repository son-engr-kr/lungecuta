# /// script
# requires-python = ">=3.12"
# dependencies = ["opencolorio", "numpy"]
# ///
"""Error of baking the ACES 2.0 output view into a 3D LUT sampled trilinearly (what grid_sample does).

Usage: uv run bench/ocio_lut_error.py
"""
import numpy as np, PyOpenColorIO as ocio
cfg = ocio.Config.CreateFromBuiltinConfig("studio-config-v5.0.0_aces-v2.1_ocio-v2.6")
t = ocio.DisplayViewTransform(); t.setSrc("DaVinci Intermediate WideGamut")
t.setDisplay("Rec.1886 Rec.709 - Display"); t.setView("ACES 2.0 - SDR 100 nits (Rec.709)")
cpu = cfg.getProcessor(t).getDefaultCPUProcessor()
for N in (33, 65, 129):
    g = np.linspace(0, 1, N, dtype=np.float32)
    grid = np.stack(np.meshgrid(g, g, g, indexing="ij"), -1).reshape(-1, 3).copy(); cpu.applyRGB(grid); lut = grid.reshape(N, N, N, 3)
    def tri(x):
        p = np.clip(x, 0, 1) * (N - 1); i0 = np.minimum(np.floor(p).astype(int), N - 2); f = p - i0; out = 0
        for a in (0, 1):
            for b in (0, 1):
                for c in (0, 1):
                    w = (f[:, 0] if a else 1 - f[:, 0]) * (f[:, 1] if b else 1 - f[:, 1]) * (f[:, 2] if c else 1 - f[:, 2])
                    out = out + w[:, None] * lut[i0[:, 0] + a, i0[:, 1] + b, i0[:, 2] + c]
        return out
    ramp = np.repeat(np.linspace(0.05, 0.9, 4096, dtype=np.float32)[:, None], 3, 1)
    ref = ramp.copy(); cpu.applyRGB(ref); e = np.abs(tri(ramp) - ref).max(1) * 1023
    rng = np.random.default_rng(1)
    y = rng.uniform(0.1, 0.75, (300000, 1)).astype(np.float32)
    x = np.clip(y + rng.normal(0, 0.03, (300000, 3)).astype(np.float32), 0, 1)
    r2 = x.copy(); cpu.applyRGB(r2); e2 = np.abs(tri(x) - r2).max(1) * 1023
    print(f"{N}^3 trilinear: grey ramp max {e.max():.2f} codes | near-neutral colours max {e2.max():.1f}, p99 {np.percentile(e2,99):.2f}, frac>1 {(e2>1).mean():.3f}")
