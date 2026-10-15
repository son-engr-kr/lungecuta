"""Whether a clip repeats its images: the best separation of a period's frames from the
rest (`lungecuta.repeats.cadence`; above 1 the clip repeats), the period and phase, so the
rule can be seen to fall in a gap between clips shot slower than a frame and the rest.

Usage: uv run python bench/repeats.py CLIP...
"""
import json
import sys
from pathlib import Path

from lungecuta.repeats import cadence, differences

out = []
for name in sys.argv[1:]:
    change = differences(Path(name))
    separation, period, phase = cadence(change)
    out.append({"clip": Path(name).name, "frames": len(change) + 1, "separation": round(separation, 3),
                "period": period, "phase": phase, "spread": [round(float(change.min()), 3), round(float(change.max()), 3)]})
print(json.dumps(out, indent=1))
