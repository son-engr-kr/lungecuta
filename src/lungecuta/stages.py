"""`lungecuta stages`: the time an edit took, by stage (docs/design/10-roadmap.md, Phase 0:
choosing material, cutting, grading, graphics, audio, rendering, each review round), from
the project's command log (`activity.py`) and the editor's journal (`journal.py`).

The time between one thing done and the next is counted to the stage of the next, since
that is what it was spent working towards: an agent deciding a cut, a person scrubbing
before a drag. A gap longer than `idle` counts to nothing, as a break, and splits the
work into sessions. That rule, and which stage each command belongs to, are *proposed*;
what is measured exactly is when each thing was done and how long each command ran."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from . import activity, journal
from .media import Project

STAGES = ("material", "cutting", "grading", "graphics", "audio", "review", "rendering")
IDLE_MINUTES = 10.0  # *proposed*: a longer gap is a break, not work

_BY_COMMAND = {
    "import": "material", "detect": "material", "root": "material", "relink": "material", "beats": "material",
    "clips": "material", "proxy": "material", "stabilize": "material",
    "licence": "material", "new-comp": "cutting", "framing": "cutting", "op graphic": "graphics",
    "graphic": "graphics", "patch-card": "graphics", "patches": "graphics", "op grade": "grading",
    "op look": "grading", "op unlook": "grading", "op sfx": "audio", "op sound": "audio", "audio-check": "audio",
    "render": "rendering", "verify": "rendering", "flash": "rendering", "fcpxml": "rendering",
}
_AUDIO_PROPS = {"gain", "fade_in", "fade_out"}


def stage_of(command: str, params: dict[str, Any]) -> str:
    """The stage a command or an editor operation belongs to."""
    if command in _BY_COMMAND:
        return _BY_COMMAND[command]
    if command in ("still", "sheet", "scope"):
        # looking at a clip is choosing material; a comp's grade is looked at in the scope
        return "material" if params.get("key") or params.get("media") else "grading" if command == "scope" else "review"
    if command.startswith("op "):
        return "audio" if params.get("prop") in _AUDIO_PROPS else "cutting"
    return "review"  # status, changes, lint, diff, validate: reading the project back


def _when(text: str) -> float:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S%z").timestamp()


def events(project: Project) -> list[dict[str, Any]]:
    """Everything done in the project, in time order: commands with how long they ran, and
    the editor's edits, undos and redos (instants)."""
    out = [{"at": _when(e["time"]), "seconds": e["seconds"], "stage": stage_of(e["command"], e["params"]),
            "by": f"agent ({e['via']})", "what": e["command"], "ok": e["ok"]} for e in activity.commands(project.directory)]
    log = journal.entries(project.directory)
    done = {e["seq"]: e for e in log}
    for entry in log:
        op = entry.get("op")
        if entry["kind"] != "edit":  # an undo or redo is of the stage of what it takes back or puts back
            op = done[entry.get("undoes") or entry.get("redoes")].get("op")
        stage = "cutting" if op is None else stage_of(f"op {op}", {})
        out.append({"at": _when(entry["time"]), "seconds": 0.0, "stage": stage, "by": "editor",
                    "what": f"{entry['kind']} {op or ''}".strip(), "ok": True})
    return sorted(out, key=lambda e: e["at"])


def stages(project: Project, *, since: str | None = None, until: str | None = None,
           idle_minutes: float = IDLE_MINUTES, comp: str | None = None) -> dict[str, Any]:
    """Minutes by stage, the sessions the work fell into, each render (a review round
    ends with one), and, with `comp`, the material the edit is made of."""
    bounds = [None if t is None else datetime.fromisoformat(t).timestamp() for t in (since, until)]
    done = [e for e in events(project)
            if (bounds[0] is None or e["at"] >= bounds[0]) and (bounds[1] is None or e["at"] <= bounds[1])]
    assert done, "nothing is logged in this project in that time"
    idle = idle_minutes * 60
    minutes = {stage: 0.0 for stage in STAGES}
    counts = {stage: {"agent": 0, "editor": 0} for stage in STAGES}
    sessions = [[done[0]["at"], done[0]["at"] + done[0]["seconds"]]]
    previous_end = done[0]["at"]
    for event in done:
        gap = event["at"] - previous_end
        if gap > idle:
            sessions.append([event["at"], event["at"]])
            gap = 0.0
        minutes[event["stage"]] += (max(0.0, gap) + event["seconds"]) / 60
        counts[event["stage"]]["editor" if event["by"] == "editor" else "agent"] += 1
        previous_end = max(previous_end, event["at"] + event["seconds"])
        sessions[-1][1] = previous_end
    stamp = lambda t: datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds")
    out: dict[str, Any] = {
        "minutes": {stage: round(value, 1) for stage, value in minutes.items()},
        "total_minutes": round(sum(minutes.values()), 1),
        "counts": counts,
        "sessions": [{"from": stamp(a), "to": stamp(b), "minutes": round((b - a) / 60, 1)} for a, b in sessions],
        "renders": [{"at": stamp(e["at"]), "seconds": e["seconds"], "ok": e["ok"]} for e in done if e["what"] == "render"],
        "idle_minutes": idle_minutes,
        "rule": "the time before each thing done counts to its stage; a gap over idle_minutes is a break (proposed)",
    }
    if comp is not None:
        out["material"] = material(project, comp)
    return out


def material(project: Project, name: str) -> dict[str, Any]:
    """What the edit is made of, so that edits are compared on comparable material."""
    from .comp import load_comp, resolve

    resolved = resolve(project, load_comp(project, name))
    return {"comp": name, "seconds": round(float(resolved.end / resolved.rate), 2),
            "shots": len(resolved.cuts), "clips": len({cut.record.key for cut in resolved.cuts}),
            "graphics": len(resolved.overlays), "sounds": len(resolved.sounds),
            "with_own_sound": sum(sound.footage for sound in resolved.sounds),
            "transitions": len(resolved.comp.transitions)}
