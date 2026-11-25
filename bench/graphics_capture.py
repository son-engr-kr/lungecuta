"""How fast a comp's graphics captures are made by one Chromium and by several
(overlays.py, CAPTURE_WORKERS), each run cold into a cache of its own outside the project.

Usage: uv run python bench/graphics_capture.py PROJECT COMP [FRAMES] [WORKERS ...]

FRAMES (default 60) is how many frames of each graphics layer, from its first, are asked
for; WORKERS (default 1 2 4 6 8) are the Chromium counts tried, after a first run with one
Chromium that every run is compared with. One Chromium is run twice, so the comparison
knows what a page varies by between two captures made the same way (a page that is not a
pure function of t, or the GPU rasterizer): every run's captures are counted, and each
layer's captures that differ from the first run's are listed with the largest difference
in codes and the most pixels differing in one capture, a layer the one-Chromium runs did not vary on
marked as such: the person reads the sizes, since a page's variation between runs is
occasional and no count of runs proves it absent. A last, warm run shows what a cache hit
costs.
"""
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from lungecuta import overlays
from lungecuta.comp import load_comp, resolve
from lungecuta.media import Project


def differences(first: dict[str, Path], other: dict[str, Path]) -> dict[str, tuple[int, int, int]]:
    """By layer: captures differing, the largest difference in codes, the most pixels
    differing in one capture."""
    out: dict[str, tuple[int, int, int]] = {}
    for path, directory in first.items():
        for capture in directory.glob("*.png"):
            twin = other[path] / capture.name
            if capture.read_bytes() == twin.read_bytes():
                continue
            a = np.asarray(Image.open(capture).convert("RGBA")).astype(np.int16)
            b = np.asarray(Image.open(twin).convert("RGBA")).astype(np.int16)
            gap = np.abs(a - b)
            if not gap.any():
                continue
            count, largest, pixels = out.get(path, (0, 0, 0))
            out[path] = (count + 1, max(largest, int(gap.max())), max(pixels, int((gap.max(axis=2) > 0).sum())))
    return out


def main() -> None:
    project = Project.load(Path(sys.argv[1]))
    resolved = resolve(project, load_comp(project, sys.argv[2]))
    frames = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    counts = [int(value) for value in sys.argv[4:]] or [1, 2, 4, 6, 8]
    indices = sorted({index for overlay in resolved.overlays
                      for index in range(overlay.first, min(overlay.end, overlay.first + frames))})
    expected = sum(len(shots) for index in indices for _, shots in overlays.visible(resolved, index))
    print(f"{len(resolved.overlays)} graphics layers, {len(indices)} comp frames, {expected} captures")
    scratch = Path(tempfile.mkdtemp())
    first = varying = None
    for run, workers in enumerate([1, *counts]):
        overlays.CAPTURE_DIR = scratch / str(run)  # absolute: the project's own cache is not touched
        start = time.perf_counter()
        entries = overlays.captures(project, resolved, indices, workers=workers)
        spent = time.perf_counter() - start
        made = {path: entry.directory for path, entry in entries.items()}
        count = sum(len(list(directory.glob("*.png"))) for directory in made.values())
        assert count == expected, f"{count} captures for {expected}"
        if first is None:
            first = made
            print(f"1 Chromium (first): {spent:.1f} s, {1000 * spent / expected:.1f} ms a capture")
            continue
        found = differences(first, made)
        if varying is None:
            varying = found
            label = "1 Chromium, again"
        else:
            label = f"{workers} Chromiums"
        listed = "; ".join(f"{path}: {c} captures, up to {g} codes, {p} pixels"
                           + ("" if path in varying else " (NOT seen varying with one Chromium)")
                           for path, (c, g, p) in found.items())
        print(f"{label}: {spent:.1f} s, {1000 * spent / expected:.1f} ms a capture; "
              f"differing from the first: {listed or 'none'}")
    start = time.perf_counter()
    overlays.captures(project, resolved, indices, workers=counts[-1])
    print(f"warm, every capture in the cache: {time.perf_counter() - start:.2f} s")
    shutil.rmtree(scratch)


if __name__ == "__main__":
    main()
