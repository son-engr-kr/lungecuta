"""Graphics layers' captures, made once and kept: each page time a render, a still, a
sheet or a check needs is captured before any frame is composed, the missing ones by
several Chromiums at once, and kept in the project's cache, so a later command captures
only what changed (05-motion-graphics.md, Captures).

A capture is a pure function of what decided it, by the page contract (`renderAt(t)`
draws frame `t` completely from `t` and its parameters, 05-motion-graphics.md): the page
file and every file it asked for, its parameters, the size and resolution it is captured
at, the photos it is given, and the browser and capture code. An entry of the cache is
named by all of these but the files, which the entry records with their hashes as the
page asked for them (`served`); a file that changed, appeared or went away since makes the
whole entry stale, and it is captured again. A page that breaks the contract (a clock, an
unseeded random number) is not detected here, as it is not by a capture made in order.
"""
from __future__ import annotations

import json
import math
import queue
import shutil
import threading
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib.metadata import version
from pathlib import Path
from typing import Any

from . import graphics
from .comp import Resolved
from .comp.parts import Overlay
from .files import content_hash_text, write_atomic
from .media import Project, load_record

CAPTURE_DIR = Path("cache") / "lungecuta" / "graphics"
# What a capture is made by besides its page: a change to how pages are captured changes this.
CAPTURE_VERSION = 1
# Chromiums capturing at once, and the fewest frames one is given of a page, so a page's
# start is paid for over many frames (bench/graphics_capture.py, bench/RESULTS.md).
CAPTURE_WORKERS = 4
LEAST_FRAMES = 24


def _name(t: float) -> str:
    """A capture's file: the page time exactly as renderAt was given it."""
    return f"{t!r}.png"


@dataclass
class Entry:
    """One graphics layer's captures in the cache, by its path of layer ids."""
    path: str
    overlay: Overlay
    size: tuple[int, int]
    directory: Path
    photos: dict[str, Path]
    page_file: Path
    served: dict[str, str | None] = field(default_factory=dict)

    def file(self, t: float) -> Path:
        return self.directory / _name(t)

    def png(self, t: float) -> bytes:
        """The capture at page time `t`, which `captures` made before the frame was composed."""
        file = self.file(t)
        assert file.is_file(), f"graphics layer {self.path} has no capture at {t!r} s: it was not asked for"
        return file.read_bytes()


def _key(project: Project, overlay: Overlay, size: tuple[int, int], photos: dict[str, Path]) -> str:
    """The name of a layer's entry: what decided its captures, its served files aside."""
    decided = {"page": overlay.layer.page, "params": overlay.layer.params, "size": list(size),
               "resolution": str(overlay.resolution),
               "photos": {key: load_record(project, key).hash for key in sorted(photos)},
               "playwright": version("playwright"), "chromium_args": graphics.CHROMIUM_ARGS,
               "capture": CAPTURE_VERSION}
    return content_hash_text(json.dumps(decided, sort_keys=True)).removeprefix("sha256:")[:32]


def _served_file(entry: Entry) -> Path:
    return entry.directory / "served.json"


def _fresh(project: Project, entry: Entry) -> bool:
    """Whether the entry's captures still hold: every file its page asked for holds what
    it held then (a file absent then is absent now)."""
    record = _served_file(entry)
    if not record.is_file():
        return False
    served = json.loads(record.read_text())
    entry.served = served
    return all(graphics.served_hash(project.directory / name) == digest for name, digest in served.items())


def _entries(project: Project, resolved: Resolved) -> dict[str, Entry]:
    from .compose import _all_overlays, photos_of

    out = {}
    for path, overlay, size in _all_overlays(resolved):
        photos = photos_of(project, overlay)
        directory = project.directory / CAPTURE_DIR / _key(project, overlay, size, photos)
        out[path] = Entry(path, overlay, size, directory, photos, project.directory / overlay.layer.page)
    return out


def visible(resolved: Resolved, index: int, prefix: str = "") -> Iterator[tuple[str, list[tuple[int, float]]]]:
    """The graphics layers visible on comp frame `index`, precomps' included at the frame
    each precomp shows, by path, each with the comp ticks and page times it is captured at:
    the frame's time, or with motion blur each of `shutter_times` (at least the layer's
    `samples`)."""
    from .compose import shutter_times

    size = tuple(resolved.comp.size)
    for overlay in resolved.overlays:
        if overlay.first <= index < overlay.end:
            yield prefix + overlay.layer.id, [(ticks, overlay.seconds_at(ticks))
                                              for ticks in shutter_times(overlay, index, size, size, at_least=overlay.samples)]
    for nest in resolved.nests_at(index):
        yield from visible(nest.resolved, nest.frame(index), f"{prefix}{nest.layer.id}/")


def _record(project: Project, entry: Entry, served: dict[Path, str | None], lock: threading.Lock) -> None:
    """Add the files a page was served to its entry's record, before the captures they
    decided are written, so no capture is kept without them."""
    names = {}
    for path, digest in served.items():
        relative = path.resolve().relative_to(project.directory.resolve()).as_posix()
        names[relative] = digest
    with lock:
        for name, digest in names.items():
            assert entry.served.get(name, digest) == digest, (
                f"{name} changed while graphics layer {entry.path} was being captured; capture again")
        entry.served |= names
        write_atomic(_served_file(entry), json.dumps(entry.served, indent=1, sort_keys=True) + "\n")


def _capture(project: Project, tasks: list[tuple[Entry, list[float]]], workers: int) -> None:
    """Capture every task's page times, each task in one page, `workers` Chromiums at once;
    the first failure stops the others at their next task and is raised."""
    work: queue.Queue = queue.Queue()
    for task in tasks:
        work.put(task)
    lock = threading.Lock()
    stop = threading.Event()

    def worker() -> None:
        with graphics.chromium() as browser:
            while not stop.is_set():
                try:
                    entry, times = work.get_nowait()
                except queue.Empty:
                    return
                served: dict[Path, str | None] = {}
                done = False
                try:
                    with graphics.opened(browser, entry.page_file, entry.size, entry.overlay.layer.params,
                                         entry.photos, resolution=entry.overlay.resolution, served=served) as page:
                        _record(project, entry, served, lock)
                        known = len(served)
                        for t in times:
                            png = graphics.frame_png(page, t)
                            if len(served) != known:  # renderAt asked for a file: recorded before the frame
                                _record(project, entry, served, lock)
                                known = len(served)
                            write_atomic(entry.file(t), png)
                    done = True
                finally:
                    if not done:
                        stop.set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(worker) for _ in range(workers)]:
            future.result()


def _tasks(needed: dict[str, set[float]], entries: dict[str, Entry], workers: int) -> list[tuple[Entry, list[float]]]:
    """The missing page times in runs: each run one page's times in order, the work split
    so every Chromium gets about twice its share, none shorter than LEAST_FRAMES."""
    total = sum(len(times) for times in needed.values())
    run = max(LEAST_FRAMES, math.ceil(total / (2 * workers)))
    tasks = []
    for path, times in needed.items():
        ordered = sorted(times)
        tasks += [(entries[path], ordered[at:at + run]) for at in range(0, len(ordered), run)]
    return sorted(tasks, key=lambda task: -len(task[1]))


def captures(project: Project, resolved: Resolved, indices: Iterable[int],
             workers: int = CAPTURE_WORKERS) -> dict[str, Entry]:
    """Every graphics layer's entry, by path, holding a capture at every page time comp
    frames `indices` show: those already in the cache kept, the rest captured now. A stale
    entry is emptied first."""
    assert workers >= 1, "capture with at least one Chromium"
    entries = _entries(project, resolved)
    if not entries:
        return entries
    for entry in entries.values():
        if entry.directory.exists() and not _fresh(project, entry):
            shutil.rmtree(entry.directory)
            entry.served = {}
        entry.directory.mkdir(parents=True, exist_ok=True)
    needed: dict[str, set[float]] = {}
    for index in indices:
        for path, shots in visible(resolved, index):
            for _, t in shots:
                if not entries[path].file(t).is_file():
                    needed.setdefault(path, set()).add(t)
    if needed:
        tasks = _tasks(needed, entries, workers)
        _capture(project, tasks, min(workers, len(tasks)))
    return entries


def captured(resolved: Resolved, index: int, entries: dict[str, Any]) -> dict[str, list[tuple[int, bytes]]]:
    """The graphics layers visible on comp frame `index`, by path, each its captures:
    (comp ticks, PNG)."""
    return {path: [(ticks, entries[path].png(t)) for ticks, t in shots] for path, shots in visible(resolved, index)}
