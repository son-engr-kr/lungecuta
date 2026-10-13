"""The journal of the editor's writes (docs/design/01-architecture.md, Files, edits and
concurrency): every file the UI writes, with its content before and after and the change
in editing terms, in `cache/journal.jsonl`. It protects the person's edits from the agent:
`lc changes` shows the agent what was done since it last asked, an overwritten change can
be recovered from it, and undo and redo walk it, each a write the agent sees too."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .files import content_hash, write_atomic

JOURNAL = Path("cache") / "journal.jsonl"
CURSOR = Path("cache") / "journal.agent"  # how far the agent has read


def entries(directory: Path) -> list[dict[str, Any]]:
    path = directory / JOURNAL
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _append(directory: Path, entry: dict[str, Any]) -> dict[str, Any]:
    path = directory / JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"seq": len(entries(directory)) + 1, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **entry}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def record(directory: Path, file: str, before: str, after: str, changes: list[str], kind: str = "edit",
           **extra: Any) -> dict[str, Any]:
    """Journal a write the UI has made to `file` (relative to the project)."""
    return _append(directory, {
        "kind": kind, "file": file, "before_hash": _hash_of(before), "after_hash": _hash_of(after),
        "before": before, "after": after, "changes": changes, **extra,
    })


def _hash_of(text: str) -> str:
    import hashlib

    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _undone(log: list[dict[str, Any]]) -> set[int]:
    """The edits an undo has taken back and no redo has put back."""
    out: set[int] = set()
    for entry in log:
        if entry["kind"] == "undo":
            out.add(entry["undoes"])
        elif entry["kind"] == "redo":
            out.discard(entry["redoes"])
    return out


def _restore(directory: Path, entry: dict[str, Any], text: str, expected_hash: str, kind: str, **extra) -> dict:
    path = directory / entry["file"]
    now = content_hash(path)
    assert now == expected_hash, (
        f"{entry['file']} changed after journal entry {entry['seq']} ({now}); undoing it would overwrite that change"
    )
    before = path.read_text(encoding="utf-8")
    write_atomic(path, text)
    return record(directory, entry["file"], before, text, [f"{kind} of: " + "; ".join(entry["changes"])], kind,
                  **extra)


def undo(directory: Path) -> dict[str, Any]:
    """Take back the UI's most recent edit that is not undone yet."""
    log = entries(directory)
    undone = _undone(log)
    candidates = [e for e in log if e["kind"] == "edit" and e["seq"] not in undone]
    assert candidates, "nothing to undo"
    target = candidates[-1]
    return _restore(directory, target, target["before"], target["after_hash"], "undo", undoes=target["seq"])


def redo(directory: Path) -> dict[str, Any]:
    """Put back the most recently undone edit, if nothing was edited since."""
    log = entries(directory)
    undone = _undone(log)
    assert undone, "nothing to redo"
    assert log[-1]["kind"] in ("undo", "redo"), "an edit came after the undo; there is nothing to redo"
    target = next(e for e in reversed(log) if e["kind"] == "edit" and e["seq"] in undone)
    undo_entry = next(e for e in reversed(log) if e["kind"] == "undo" and e["undoes"] == target["seq"])
    return _restore(directory, target, target["after"], undo_entry["after_hash"], "redo", redoes=target["seq"])


def changes(directory: Path, *, peek: bool = False) -> dict[str, Any]:
    """What the UI wrote since the agent last asked; asking moves the agent's place on,
    unless `peek`."""
    cursor_path = directory / CURSOR
    since = int(cursor_path.read_text()) if cursor_path.exists() else 0
    new = [e for e in entries(directory) if e["seq"] > since]
    if new and not peek:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(cursor_path, str(new[-1]["seq"]))
    return {"since": since, "entries": [{k: e[k] for k in ("seq", "time", "kind", "file", "changes", "after_hash")}
                                        for e in new]}
