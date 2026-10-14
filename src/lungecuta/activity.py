"""What was done in a project and when (docs/design/10-roadmap.md, Phase 0: the time is
recorded by stage). Every `lungecuta` command that runs in a project, from a shell or
through `lungecuta mcp`, is logged with when it started, how long it ran, what it was
given and whether it succeeded, in `cache/lungecuta/commands.jsonl`; the editor's edits
are in its journal (`journal.py`). The log is local to the machine, like the rest of the
cache, and is never committed."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

LOG = Path("cache") / "lungecuta" / "commands.jsonl"

# How commands reach lungecuta: `lungecuta mcp` sets it for the commands it runs.
via = "cli"


def record(directory: Path, command: str, params: dict[str, Any], started: float, ok: bool) -> None:
    """Append one command to the project's log. One line is written with one call, so two
    processes logging at once do not interleave their lines."""
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(started)),
             "seconds": round(time.time() - started, 3), "command": command, "via": via, "ok": ok,
             "params": {name: value for name, value in params.items() if value not in (None, (), [], False)}}
    path = directory / LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(entry, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(descriptor, line)
    finally:
        os.close(descriptor)


def commands(directory: Path) -> list[dict[str, Any]]:
    path = directory / LOG
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
