"""The guide a coding agent reads in a project (docs/design/09-agent-interface.md, Working
in a project): `AGENTS.md`, which Codex reads, and `CLAUDE.md`, which Claude Code reads and
which imports `AGENTS.md`. `lc init` writes both; `lc guide --write` brings the guide up to
this version of lungecuta. The guide sits between two markers in `AGENTS.md`, so whatever
the person writes outside them is kept."""
from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from .files import write_atomic

START = "<!-- lc:guide start: written by lc guide --write; edit outside these markers -->"
END = "<!-- lc:guide end -->"
IMPORT = "@AGENTS.md"

GUIDE = """\
# Working in this lungecuta project (lungecuta {version})

This directory is a lungecuta project: a music-driven video edit kept as JSON files that
you, an agent, and a person edit together. The person watches and listens in the editor
(`lc serve`); you read and write the files and use `lc` to see and check what they mean.
Every `lc` command prints one JSON object; `lc COMMAND --help` says exactly what it does,
and `lc schema` prints the JSON Schema of every file.

## Files

- `project.json`: colour settings, media roots, the comps, lint thresholds, deliverables.
- `comps/NAME.json`: a comp. Layers are listed bottom first; every time is a literal.
- `media/KEY.json`: one record per imported file, written by `lc import`; not edited by hand.
- `styles/`, `grades/`, `graphics/`, `luts/`: what comps refer to by name.
- `cache/` and `roots.local.json`: rebuilt or machine-specific, never committed.

## Time literals

`f:120` frames, `4.5s` seconds, `b:16+0.5` beat 16 plus half the next beat interval,
`bar:4` the fifth downbeat (`bar:0` is the first), `tc:00:00:04:15` timecode. In a comp a
position lands on the first frame at or after it. A layer's `in`, `out` and keys are
spans from its `start`: `b:4` there is four beats on.

## Arriving

`lc status` lists the media (and which clips are usable), the comps with their length,
layers and markers, the editor changes you have not seen, and whether each render in the
cache still matches its comp. `lc validate` checks every file.

## Every edit

1. `lc changes` before you edit: what the person did in the editor since you last asked.
   Never write over it.
2. Prefer `lc op OP COMP ...` (move, trim, slip, slide, roll, ripple-delete, split, snap,
   place, key, unkey, set, remap, style, unstyle, grade, masks, transition,
   untransition): the editor's own functions, written only if the file is still the one
   read, the change printed in editing terms. Edit the JSON for anything else, then run
   `lc validate`.
3. Look at what you changed. `lc sheet COMP --every b:1 --from P --to P` over the range,
   `lc still COMP --at P` on the frames that matter (cuts, hits, titles, faces),
   `lc scope COMP --at P` for levels. Trust the printed numbers over your reading of an
   image.
4. Sound changed: `lc audio-check COMP` (cut offsets to beats, level per half beat).
5. Before showing the person: `lc lint COMP`, and `lc diff COMP` for what changed since
   the last commit.
6. Delivering: `lc render COMP --preset P` (`web-h264`, `web-hevc10`, `master-prores-hq`,
   `prores-4444-alpha`), then `lc verify FILE --comp COMP --preset P` and `lc flash FILE`.
   Both exit with status 1 when the file fails.
7. Commit each review round (the project is meant to be a git repository); never
   overwrite a version the person has seen.

## What you cannot judge

You cannot watch playback or hear. Report what you measured (a cut 12 ms after the
beat, a title on screen for 0.4 s, a true peak of -1.0 dBTP) and leave whether a ramp
feels right or a drop hits hard to the person. Where a number is not measured or
published, say so instead of choosing one.

## Rules

- A clip imported as undecided in colour or time stays unusable until the person
  decides it; never guess its colour space.
- Footage and music are read where they are and never copied into the project.
- Nothing in the project runs as Python: graphics are web pages, run in a sandboxed
  browser with no network.
"""


def guide_text() -> str:
    return GUIDE.format(version=version("lungecuta"))


def _block() -> str:
    return f"{START}\n{guide_text()}{END}\n"


def write_guide(directory: Path) -> dict[str, Any]:
    """Write the guide into `directory`'s AGENTS.md, replacing an older one between the
    markers and keeping everything outside them, and make CLAUDE.md import AGENTS.md."""
    agents, claude = directory / "AGENTS.md", directory / "CLAUDE.md"
    written = {}
    if not agents.exists():
        write_atomic(agents, _block())
        written["AGENTS.md"] = "created"
    else:
        text = agents.read_text()
        assert text.count(START) == text.count(END) <= 1, f"{agents} holds the guide's markers more than once"
        if START in text:
            before, rest = text.split(START, 1)
            after = rest.split(END, 1)[1].removeprefix("\n")
            updated = before + _block() + after
        else:
            updated = text.rstrip("\n") + "\n\n" + _block()
        written["AGENTS.md"] = "unchanged" if updated == text else "updated"
        if updated != text:
            write_atomic(agents, updated)
    if not claude.exists():
        write_atomic(claude, IMPORT + "\n")
        written["CLAUDE.md"] = "created"
    elif IMPORT not in claude.read_text().split():
        write_atomic(claude, claude.read_text().rstrip("\n") + "\n\n" + IMPORT + "\n")
        written["CLAUDE.md"] = "updated"
    else:
        written["CLAUDE.md"] = "unchanged"
    return {"directory": str(directory), "files": written, "lungecuta": version("lungecuta")}
