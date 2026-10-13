"""How many files `lc import` should import at once, and that the records do not change.

Usage: uv run python bench/import_jobs.py WORKDIR FILE...
  (FILE... under one directory, with their sidecars; WORKDIR is emptied)

Imports FILE... into a fresh project with 1, 2, 3, 4, then 1, 2 and 3 again files at once and checks that every
run's records (but the time they were made) and proxies (frame by frame) are the
one-at-a-time run's.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from lungecuta import media
from lungecuta.project import init_project


def framemd5(path: Path) -> list[str]:
    lines = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                           capture_output=True, text=True, check=True).stdout.splitlines()
    return [line.rsplit(",", 1)[-1].strip() for line in lines if line and not line.startswith("#")]


def main() -> None:
    work, files = Path(sys.argv[1]), [Path(f).resolve() for f in sys.argv[2:]]
    shutil.rmtree(work, ignore_errors=True)
    reference = None
    for jobs in (1, 2, 3, 4, 1, 2, 3):
        directory = init_project(work / f"run{len(list(work.glob('*.lc'))) if work.exists() else 0}-jobs{jobs}.lc")
        media.set_root(directory, "samples", files[0].parent)
        media.IMPORT_JOBS = jobs
        t = time.perf_counter()
        records = media.import_files(media.Project.load(directory), files, root="samples")
        seconds = time.perf_counter() - t
        made = [(json.loads(record.model_dump_json()), framemd5(directory / record.proxy.path)) for record in records]
        reference = reference or made
        print(f"{jobs} at once: {seconds:5.1f} s for {len(files)} files, same records and proxies: {made == reference}")


if __name__ == "__main__":
    main()
