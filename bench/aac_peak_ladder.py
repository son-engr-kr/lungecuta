"""How a lossy encode's true peak follows the master's ceiling: the comp's mix mastered under
each ceiling given, encoded as the preset encodes its audio, decoded, and its true peak
measured with where it is, so the step `delivered_audio` takes between passes can rest on
how the encoded peak actually moves (`src/lungecuta/mixdown.py`).

Usage: uv run python bench/aac_peak_ladder.py PROJECT COMP PRESET OUT_DIR CEILING...
"""
import json
import sys
from pathlib import Path

import numpy as np

from lungecuta.audio import master, true_peak_db
from lungecuta.comp import load_comp, resolve
from lungecuta.encode import PRESETS
from lungecuta.media import Project
from lungecuta.mixdown import MIX_RATE, mix
from lungecuta.music import wav_bytes
from lungecuta.tools import run


def main(project_dir: str, comp: str, preset_name: str, out_dir: str, ceilings: list[float]) -> None:
    project = Project.load(Path(project_dir))
    preset = PRESETS[preset_name]
    mixed = mix(project, resolve(project, load_comp(project, comp)))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for ceiling in ceilings:
        signal, chain = master(mixed, MIX_RATE, target_lufs=preset.loudness, ceiling_dbtp=ceiling)
        wav, encoded = out / "ladder.wav", out / "ladder.m4a"
        wav.write_bytes(wav_bytes(signal, MIX_RATE))
        run(["ffmpeg", "-v", "error", "-y", "-i", str(wav), *preset.audio, str(encoded)])
        decoded = np.frombuffer(run(["ffmpeg", "-v", "error", "-i", str(encoded), "-f", "f32le", "-"]).stdout,
                                np.float32).reshape(-1, 2)
        where = int(np.abs(decoded).max(axis=1).argmax())
        rows.append({"ceiling": ceiling, "master_true_peak": round(true_peak_db(signal, MIX_RATE), 3),
                     "encoded_true_peak": round(true_peak_db(decoded, MIX_RATE), 3),
                     "encoded_sample_peak_at_s": round(where / MIX_RATE, 3), "lufs": chain.get("loudness_after")})
        print(json.dumps(rows[-1]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], [float(c) for c in sys.argv[5:]])
