"""The guide a coding agent reads in a project (docs/design/09-agent-interface.md, Working
in a project): `AGENTS.md`, which Codex reads, and `CLAUDE.md`, which Claude Code reads and
which imports `AGENTS.md`; and `.mcp.json` and `.codex/config.toml`, which start `lungecuta mcp`
for Claude Code and for Codex (Codex reads a project's own config once the project is
trusted). `lungecuta init` writes them; `lungecuta guide --write` brings the guide up to this version of
lungecuta. The guide sits between two markers in `AGENTS.md`, so whatever the person
writes outside them is kept, and both configs keep the other servers they list."""
from __future__ import annotations

import json
import tomllib
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .files import json_text, write_atomic

START = "<!-- lungecuta:guide start: written by lungecuta guide --write; edit outside these markers -->"
END = "<!-- lungecuta:guide end -->"
IMPORT = "@AGENTS.md"
SERVER = {"command": "lungecuta", "args": ["mcp"]}  # lungecuta on the PATH; it finds the project from where it starts
# How the repository puts lungecuta on the PATH, with the versions uv.lock pins (README.md says it too)
# and the Python .python-version names, which uv tool install does not read on its own
INSTALL = "uv tool install --editable . --python 3.12 --constraints <(uv export --frozen --no-dev --no-emit-project --no-hashes)"
# What puts it there, for a command that is missing or that a shell does not find
PUT_ON_PATH = f"`{INSTALL}` in the lungecuta repository; if it is installed already, `uv tool update-shell` and a new terminal"

GUIDE = """\
# Working in this lungecuta project (lungecuta {version})

This directory is a lungecuta project: a music-driven video edit kept as JSON files that
you, an agent, and a person edit together. The person watches and listens in the editor
(`lungecuta serve`); you read and write the files and use `lungecuta` to see and check what they mean.
Every `lungecuta` command prints one JSON object; `lungecuta COMMAND --help` says exactly what it does,
`lungecuta commands` lists every command with its arguments and options as JSON, and
`lungecuta schema` prints the JSON Schema of every file. The same commands are MCP tools when
`lungecuta mcp` runs (this project's `.mcp.json` starts it for Claude Code, and its
`.codex/config.toml` for Codex once the project is trusted): `still`,
`op_move` and so on, with the pictures they write returned as images.

## Files

- `project.json`: colour settings, media roots, the comps, lint thresholds, deliverables.
- `comps/NAME.json`: a comp. Layers are listed bottom first; every time is a literal.
- `media/KEY.json`: one record per imported file, written by `lungecuta import`; not edited by hand.
- `styles/`, `grades/`, `graphics/`, `luts/`: what comps refer to by name.
- `cache/` and `roots.local.json`: rebuilt or machine-specific, never committed.

## Time literals

`f:120` frames, `4.5s` seconds, `b:16+0.5` beat 16 plus half the next beat interval,
`bar:4` the fifth downbeat (`bar:0` is the first), `tc:00:00:04:15` timecode. In a comp a
position lands on the first frame at or after it. A layer's `in`, `out` and keys are
spans from its `start`: `b:4` there is four beats on.

## Arriving

`lungecuta status` lists the media (and which clips are usable), the comps with their length,
layers and markers, the editor changes you have not seen, and whether each render in the
cache still matches its comp. `lungecuta validate` checks every file. `lungecuta doctor` checks
the machine (the pinned ffmpeg, Chromium, the beat model) and says what to run when a part is
missing; `lungecuta setup` fetches it.

## Every edit

1. `lungecuta changes` before you edit: what the person did in the editor since you last asked.
   Never write over it.
2. Prefer `lungecuta op OP COMP ...` (move, trim, slip, slide, roll, ripple-delete, split, snap,
   place, graphic, key, unkey, move-key, set, remap, style, unstyle, grade, look, unlook,
   masks, transition, untransition, sfx): the editor's own functions, written only if the
   file is still the one read, the change printed in editing terms. Edit the JSON for anything
   else, then run `lungecuta validate`.
3. Look at what you changed. `lungecuta sheet COMP --every b:1 --from P --to P` over the range,
   `lungecuta still COMP --at P` on the frames that matter (cuts, hits, titles, faces),
   `lungecuta scope COMP --at P` for levels, and `lungecuta framing COMP LAYER` after a transform
   reframes footage (it lists the frames where the source's edge shows). Trust the
   printed numbers over your reading of an image.
4. Sound changed: `lungecuta audio-check COMP` (cut offsets to beats and to the mix's onsets,
   level per half beat).
5. Before showing the person: `lungecuta lint COMP`, and `lungecuta diff COMP` for what changed since
   the last commit.
6. Delivering: `lungecuta render COMP --preset P` (`web-h264`, `web-hevc10`, `master-prores-hq`,
   `prores-4444-alpha`), then `lungecuta verify FILE --comp COMP --preset P` and `lungecuta flash FILE`.
   Both exit with status 1 when the file fails.
7. Commit each review round (the project is meant to be a git repository); never
   overwrite a version the person has seen.

## Building a cut

Choose in points from the footage itself: `lungecuta sheet --media KEY --every f:15` labels each
frame with its seconds. Then one `place` per shot, on the beat grid:
`lungecuta op place COMP KEY --at b:8 --for b:4 --source 2.5s` puts KEY from 2.5 s into it at
beat 8 for four beats; `--to b:4` ends a layer at a position instead, as the first shot
from `0s` needs (no beat span starts before the first beat). A later layer is drawn over
an earlier one, so shots that meet at cuts need no ordering. `lungecuta op graphic COMP
graphics/title.html --at 0s --to b:4 --params '{{...}}'` puts a title page on top.
`lungecuta audio-check COMP` then gives every cut's offset to its beat.

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
    written[".mcp.json"] = _claude_server(directory / ".mcp.json")
    written[".codex/config.toml"] = _codex_server(directory / ".codex" / "config.toml")
    return {"directory": str(directory), "files": written, "lungecuta": version("lungecuta")}


def _claude_server(config: Path) -> str:
    servers = json.loads(config.read_text()) if config.exists() else {}
    if servers.get("mcpServers", {}).get("lungecuta") is not None:
        return "unchanged"
    servers.setdefault("mcpServers", {})["lungecuta"] = SERVER
    write_atomic(config, json_text(servers))
    return "updated" if len(servers["mcpServers"]) > 1 else "created"


def _codex_server(config: Path) -> str:
    """The server as a table appended to the TOML; JSON's strings and arrays of strings
    are TOML's too. The result is read back, so a file the table cannot join fails here."""
    text = config.read_text() if config.exists() else ""
    if tomllib.loads(text).get("mcp_servers", {}).get("lungecuta") is not None:
        return "unchanged"
    table = (f"[mcp_servers.lungecuta]\ncommand = {json.dumps(SERVER['command'])}\n"
             f"args = {json.dumps(SERVER['args'])}\n")
    updated = text.rstrip("\n") + "\n\n" + table if text.strip() else table
    assert tomllib.loads(updated)["mcp_servers"]["lungecuta"] == SERVER, updated
    config.parent.mkdir(exist_ok=True)
    write_atomic(config, updated)
    return "updated" if text.strip() else "created"
