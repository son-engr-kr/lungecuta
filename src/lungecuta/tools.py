"""ffmpeg and ffprobe, called as subprocesses (the Homebrew build is GPL; see
docs/design/08-deliver.md#licences)."""
from __future__ import annotations

import shutil
import subprocess


def tool(name: str) -> str:
    path = shutil.which(name)
    assert path, f"{name} is not on PATH; lungecuta calls ffmpeg and ffprobe as subprocesses"
    return path


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run a tool and fail with its error output when it fails."""
    done = subprocess.run([tool(args[0]), *args[1:]], capture_output=capture, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        stderr = done.stderr.decode(errors="replace").strip() if capture else ""
        raise AssertionError(f"{args[0]} failed ({done.returncode}): {stderr}\ncommand: {' '.join(args)}")
    return done
