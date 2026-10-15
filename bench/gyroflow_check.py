"""What a clip made by `lungecuta stabilize` changes against its original, frame for frame:
the mean signed difference of each Y'CbCr plane in codes (a shift of level, which a warp
alone does not make on average), the local displacement at nine patches by phase
correlation (the warp), and the share of the frame's border that went black (borders the
warp uncovered).

Usage: uv run python bench/gyroflow_check.py PROJECT KEY DERIVED [FRAME...]
"""
import json
import sys
from pathlib import Path

import numpy as np

from lungecuta.media import Project, load_record, original
from lungecuta.tools import run


def planes(path: Path, frame: int, size: tuple[int, int], pix_fmt: str) -> list[np.ndarray]:
    """Frame `frame` as 4:2:2 10-bit planes: as coded when the file is, else converted by
    ffmpeg (which changes chroma, not luma), so compare chroma only between 4:2:2 files."""
    raw = run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-vf", f"select=eq(n\\,{frame})",
               "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "yuv422p10le", "-"]).stdout
    width, height = size
    data = np.frombuffer(raw, np.uint16).astype(np.float64)
    assert data.size == width * height * 2, f"frame {frame}: {data.size} samples"
    luma, rest = data[: width * height].reshape(height, width), data[width * height:]
    half = width // 2
    return [luma, rest[: height * half].reshape(height, half), rest[height * half:].reshape(height, half)]


def displacement(a: np.ndarray, b: np.ndarray) -> list[int]:
    """The whole-pixel shift taking patch `b` onto patch `a`, by phase correlation."""
    spectrum = np.fft.fft2(a - a.mean()) * np.conj(np.fft.fft2(b - b.mean()))
    peak = np.fft.ifft2(spectrum / (np.abs(spectrum) + 1e-9)).real
    i, j = np.unravel_index(int(np.argmax(peak)), peak.shape)
    return [int(i if i < peak.shape[0] // 2 else i - peak.shape[0]), int(j if j < peak.shape[1] // 2 else j - peak.shape[1])]


def main(project_dir: str, key: str, derived: str, frames: list[int]) -> None:
    project = Project.load(Path(project_dir))
    a, b = load_record(project, key), load_record(project, derived)
    video = a.probe.video
    width, height = video.size
    out = {"original": key, "derived": derived, "derivation": b.derivation.model_dump(mode="json"), "frames": []}
    for frame in frames:
        pa = planes(original(project, a), frame, video.size, video.pix_fmt)
        pb = planes(original(project, b), frame, b.probe.video.size, b.probe.video.pix_fmt)
        border = np.concatenate([pb[0][:8].ravel(), pb[0][-8:].ravel(), pb[0][:, :8].ravel(), pb[0][:, -8:].ravel()])
        patches = {f"{r},{c}": displacement(pa[0][r * height // 4 - 128: r * height // 4 + 128, c * width // 4 - 128: c * width // 4 + 128],
                                            pb[0][r * height // 4 - 128: r * height // 4 + 128, c * width // 4 - 128: c * width // 4 + 128])
                   for r in (1, 2, 3) for c in (1, 2, 3)}
        out["frames"].append({
            "frame": frame, "pix_fmts": [video.pix_fmt, b.probe.video.pix_fmt],
            "mean_signed": {name: round(float((y - x).mean()), 3) for name, x, y in zip("Y Cb Cr".split(), pa, pb)},
            "mean_abs_y": round(float(np.abs(pb[0] - pa[0]).mean()), 2),
            "border_black": round(float((border <= 64).mean()), 4),
            "shift_rows_cols": patches,
        })
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], [int(f) for f in sys.argv[4:]] or [0, 350, 704])
