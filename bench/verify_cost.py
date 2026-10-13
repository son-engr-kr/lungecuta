"""What `lc verify` costs with more runs of sampled frames at once, and that the report does
not change.

Usage: uv run python bench/verify_cost.py PROJECT COMP FILE PRESET
  (FILE rendered from COMP with PRESET by `lc render`)

Verifies FILE with 1, 2, 4, 8 and 15 jobs at once and checks that every report is the
one-job report.
"""
import json
import sys
import time
from pathlib import Path

from lungecuta import compose, verify
from lungecuta.media import Project


def main() -> None:
    project, comp, file, preset = Project.load(Path(sys.argv[1])), sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    reference = None
    for jobs in (1, 2, 4, 8, 15):
        compose.FRAMES_AT_ONCE = jobs
        t = time.perf_counter()
        report = json.dumps(verify.verify(project, comp, file, preset), sort_keys=True)
        reference = reference or report
        print(f"{jobs:2d} jobs: {time.perf_counter() - t:5.1f} s, ok {json.loads(report)['ok']}, "
              f"same report: {report == reference}")


if __name__ == "__main__":
    main()
