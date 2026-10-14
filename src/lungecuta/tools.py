"""ffmpeg and ffprobe: one pinned build, fetched on first use into lungecuta's cache and
checked by size and SHA-256, never whatever is on the PATH, so every machine decodes and
encodes with the same code (two builds were found to decode the same MP3 to different
samples, `bench/ffmpeg_builds.py`). The build is GPL and is called as a subprocess only
(docs/design/08-deliver.md#licences)."""
from __future__ import annotations

import fcntl
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Binary:
    """One program of the build: its archive as published, and the program it holds."""

    name: str
    archive_size: int
    archive_sha256: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Build:
    version: str
    system: str
    machine: str
    source: str
    binaries: tuple[Binary, ...]


# Martin Riedl's static release build for macOS on Apple Silicon (https://ffmpeg.martin-riedl.de),
# signed with its builder's Developer ID; the archive hashes are the ones published beside the
# archives, the program hashes those of the programs they held when fetched on 2026-10-14.
BUILD = Build(
    version="9.0.2",
    system="darwin",
    machine="arm64",
    source="https://ffmpeg.martin-riedl.de/download/macos/arm64/1789931890_9.0.2/",
    binaries=(
        Binary("ffmpeg", 28395699, "c8ed4c4e6978a03c485edbfe4e0a5dc2380f8a30bba5150531b31b094492d924",
               66332880, "2e11c6f90993cdb79fff84d3f90044d28316b310e75b3e030cfc9a54f2c9d384"),
        Binary("ffprobe", 28317701, "fcbe839537485eaee7a7a8bc5cbc0f90d53617e80943e8a5b2e31cb851197ea6",
               66158064, "2738aa46a7f9acbc8ab09a6715554c90f7de603171ac403726765685df6a0059"),
    ),
)

CACHE = Path.home() / "Library" / "Caches" / "lungecuta"

_CHUNK = 1 << 20


def directory(build: Build = BUILD) -> Path:
    """Where the build's programs are kept."""
    return CACHE / "ffmpeg" / build.version


def supported(build: Build = BUILD) -> bool:
    return sys.platform == build.system and platform.machine() == build.machine


def missing(build: Build = BUILD, place: Path | None = None) -> list[Binary]:
    """The programs not in place yet, judged by size alone (hashing 66 MB on every call
    would cost more than the call; `verify` hashes them)."""
    place = place or directory(build)
    return [b for b in build.binaries
            if not ((place / b.name).is_file() and (place / b.name).stat().st_size == b.size)]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(build: Build = BUILD, place: Path | None = None, *, opener=urllib.request.urlopen) -> list[str]:
    """Download and unpack the programs not in place yet, each archive and each program
    checked against its size and SHA-256 before it is moved into place, under a lock so
    that commands started together download once. Returns the names fetched."""
    place = place or directory(build)
    place.mkdir(parents=True, exist_ok=True)
    fetched = []
    with (place / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for binary in missing(build, place):
            print(f"lungecuta: fetching {binary.name} {build.version} ({binary.archive_size / 1e6:.0f} MB) "
                  f"from {build.source}", file=sys.stderr)
            archive = place / f"{binary.name}.zip.part"
            # The builder's CDN refuses Python's own User-Agent (HTTP 403, 2026-10-14).
            request = urllib.request.Request(build.source + f"{binary.name}.zip", headers={"User-Agent": "lungecuta"})
            with opener(request, timeout=60) as response, archive.open("wb") as out:
                while chunk := response.read(_CHUNK):
                    out.write(chunk)
            assert archive.stat().st_size == binary.archive_size and _sha256(archive) == binary.archive_sha256, \
                f"the {binary.name} archive from {build.source} is not the pinned one"
            part = place / f"{binary.name}.part"
            with zipfile.ZipFile(archive) as zipped:
                assert zipped.namelist() == [binary.name], f"{binary.name}.zip holds {zipped.namelist()}"
                part.write_bytes(zipped.read(binary.name))
            archive.unlink()
            assert part.stat().st_size == binary.size and _sha256(part) == binary.sha256, \
                f"the {binary.name} in the archive is not the pinned program"
            part.chmod(0o755)
            os.replace(part, place / binary.name)
            fetched.append(binary.name)
    return fetched


def verify(build: Build = BUILD, place: Path | None = None) -> dict[str, bool]:
    """Each program in place and identical to the pinned one, by SHA-256."""
    place = place or directory(build)
    return {b.name: (place / b.name).is_file() and _sha256(place / b.name) == b.sha256 for b in build.binaries}


def tool(name: str) -> str:
    """ffmpeg and ffprobe from the pinned build, fetched first if they are not in place;
    any other program (git, for lungecuta diff) from the PATH."""
    if name not in {b.name for b in BUILD.binaries}:
        path = shutil.which(name)
        assert path, f"{name} is not on the PATH"
        return path
    assert supported(), (f"lungecuta pins an ffmpeg build for {BUILD.system} on {BUILD.machine} only, "
                         f"not {sys.platform} on {platform.machine()}")
    if missing():
        fetch()
    return str(directory() / name)


def run(args: list[str], *, capture: bool = True) -> subprocess.CompletedProcess[bytes]:
    """Run a tool and fail with its error output when it fails."""
    done = subprocess.run([tool(args[0]), *args[1:]], capture_output=capture, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        stderr = done.stderr.decode(errors="replace").strip() if capture else ""
        raise AssertionError(f"{args[0]} failed ({done.returncode}): {stderr}\ncommand: {' '.join(args)}")
    return done
