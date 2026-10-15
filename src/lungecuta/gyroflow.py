"""Gyroflow (https://gyroflow.xyz), the open-source stabilizer that reads the gyro, IBIS and
lens data a camera records, as `lungecuta stabilize` runs it: one pinned release, fetched on
first use into lungecuta's cache and checked by SHA-256 as ffmpeg is (`tools.py`), never the
copy in /Applications. It is GPL-3.0, so it is run as a subprocess only, never linked or
copied (docs/design/08-deliver.md#licences)."""
from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .tools import CACHE, _CHUNK, _sha256


@dataclass(frozen=True)
class Release:
    version: str
    url: str
    size: int
    sha256: str  # the disk image's, as GitHub publishes it beside the asset
    executable: str  # inside the disk image
    executable_size: int
    executable_sha256: str  # the program the image held when it was fetched on 2026-10-15


RELEASE = Release(
    version="1.6.3",
    url="https://github.com/gyroflow/gyroflow/releases/download/v1.6.3/Gyroflow-mac-universal.dmg",
    size=88593266,
    sha256="fbe26793c639f1410d8995debad188721587108cb6a74224e879f79e4bb8a1c0",
    executable="Gyroflow.app/Contents/MacOS/gyroflow",
    executable_size=100027824,
    executable_sha256="294fe1d939a13d7617ebcb0eebd064ccc90e4efed4326030635886b315bdf3b1",
)


def directory(release: Release = RELEASE) -> Path:
    return CACHE / "gyroflow" / release.version


def executable_path(release: Release = RELEASE) -> Path:
    return directory(release) / release.executable


def ready(release: Release = RELEASE) -> bool:
    """In place, judged by the executable's size (`verify` hashes it)."""
    path = executable_path(release)
    return path.is_file() and path.stat().st_size == release.executable_size


def verify(release: Release = RELEASE) -> bool:
    path = executable_path(release)
    return path.is_file() and _sha256(path) == release.executable_sha256


def fetch(release: Release = RELEASE, *, opener=urllib.request.urlopen) -> bool:
    """Download the disk image, check it, copy the app out of it and check its program,
    under a lock so that commands started together download once. Returns whether it
    fetched anything."""
    place = directory(release)
    place.mkdir(parents=True, exist_ok=True)
    with (place / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if ready(release):
            return False
        print(f"lungecuta: fetching Gyroflow {release.version} ({release.size / 1e6:.0f} MB) from {release.url}",
              file=sys.stderr)
        image = place / "gyroflow.dmg.part"
        request = urllib.request.Request(release.url, headers={"User-Agent": "lungecuta"})
        with opener(request, timeout=60) as response, image.open("wb") as out:
            while chunk := response.read(_CHUNK):
                out.write(chunk)
        assert image.stat().st_size == release.size and _sha256(image) == release.sha256, \
            f"the disk image from {release.url} is not the pinned one"
        app = release.executable.split("/")[0]
        with tempfile.TemporaryDirectory(dir=place) as mount:
            subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount, str(image)],
                           check=True, capture_output=True, stdin=subprocess.DEVNULL)
            staged = place / f"{app}.part"
            shutil.rmtree(staged, ignore_errors=True)
            copied = subprocess.run(["ditto", str(Path(mount) / app), str(staged)], capture_output=True)
            subprocess.run(["hdiutil", "detach", mount], check=True, capture_output=True, stdin=subprocess.DEVNULL)
            assert copied.returncode == 0, f"copying {app} out of the disk image failed: {copied.stderr.decode()}"
        image.unlink()
        program = staged / Path(release.executable).relative_to(app)
        assert program.stat().st_size == release.executable_size and _sha256(program) == release.executable_sha256, \
            f"the program in {app} is not the pinned one"
        os.replace(staged, place / app)
    return True


def executable() -> str:
    """The pinned Gyroflow's program, fetched first if it is not in place."""
    if not ready():
        fetch()
    return str(executable_path())


def run(args: list[str]) -> str:
    """Run Gyroflow headless (it runs without a window when given arguments) and fail with
    its log when it fails; its log is returned."""
    done = subprocess.run([executable(), *args], capture_output=True, stdin=subprocess.DEVNULL)
    log = (done.stdout + done.stderr).decode(errors="replace")
    assert done.returncode == 0, f"Gyroflow failed ({done.returncode}):\n{log[-4000:]}"
    return log
