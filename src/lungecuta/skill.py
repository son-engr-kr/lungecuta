"""The lungecuta skill: what Claude Code and Codex read, anywhere on this machine, when the
person asks to edit a video (docs/design/09-agent-interface.md, Getting started). It takes
an agent from an installed lungecuta to a first project; inside a project, the project's
own guide (`guide.py`) takes over. `lungecuta setup` writes it into each agent's personal
skills directory, `~/.claude/skills/lungecuta/` for Claude Code and
`~/.agents/skills/lungecuta/` for Codex, both of which pick up a new skill without a
restart; `lungecuta doctor` checks that both hold this version's text."""
from __future__ import annotations

from importlib.metadata import version
from pathlib import Path

from .files import write_atomic

# Each agent's personal skills directory, relative to the home directory, as its documentation names it
PLACES = {"Claude Code": Path(".claude/skills/lungecuta"), "Codex": Path(".agents/skills/lungecuta")}

SKILL = """\
---
name: lungecuta
description: Edit a short, music-driven video with lungecuta, the video editor installed on this machine. Starts a project from the person's footage and a song, cuts it on the beat, grades camera log, opens the editor in the browser, renders and checks the video. Use when the person wants to edit or make a video, a reel or a music video, or mentions lungecuta. Inside a lungecuta project, the project's AGENTS.md is the full guide.
---

<!-- Written by lungecuta setup, which replaces it when lungecuta is updated. -->

# Editing a video with lungecuta (lungecuta {version})

lungecuta is installed here as the `lungecuta` command. Every command prints one JSON
object, and `lungecuta COMMAND --help` says exactly what it does.

The person may not be a programmer. Talk with them in their language and in plain words,
run every command yourself, and never ask them to edit a file or type a command. Say what
you are about to download or create before you do it. macOS may ask them to allow access
to a folder (Documents, Desktop, an external drive): they click Allow.

## 1. The machine

Run `lungecuta doctor`. For each check that fails, run its `fix` (`lungecuta setup`
downloads the pinned ffmpeg, a headless Chromium for titles and the beat model, once),
then run doctor again. lungecuta runs on macOS on Apple Silicon only; if the platform
check fails, tell the person and stop.

## 2. The project

Run `lungecuta status`. If it describes a project, you are in one: its `AGENTS.md`
(`CLAUDE.md` imports it) is your guide from here on. If it fails with `no project.json`,
there is none yet, so make one. Ask the person, in one message:

- what the video is, for the project's name (NAME below, short, no spaces);
- which folder holds the footage (it is read where it is, never copied or moved);
- which song file;
- whether the video is landscape (16:9) or vertical (9:16, for a reel or a short), and
  about how long.

Then, with PROJECT as `~/Movies/NAME.lungecuta` unless the person wants it elsewhere:

```
lungecuta init PROJECT
lungecuta root footage FOOTAGE_FOLDER --project PROJECT
lungecuta root music SONG_FOLDER --project PROJECT
lungecuta import CLIP... --root footage --project PROJECT
lungecuta import SONG --root music --key song --project PROJECT
lungecuta status --project PROJECT
```

`import` takes the video files (a camera's XML files beside them are read on their own)
and checks every clip, so it takes a while; tell the person. `status` then says which
clips are usable. A clip whose colour space could not be identified is imported as
undecided: tell the person which clips and why (the evidence is in its record under
`media/`), and leave them out. Never guess a colour space.

Make the comp the cut goes in, at the footage's frame rate (`status` lists each clip's
`rate`):

```
lungecuta new-comp main --size 1920x1080 --rate RATE --end b:BEATS --music song --project PROJECT
```

`1080x1920` for vertical. BEATS is the length in beats: `status` gives the song's tempo,
so 30 s at 120 BPM is `b:60`.

## 3. Moving into the project

The project's guide, and its MCP tools that return frames as images, load when Claude
Code or Codex starts in the project folder. Tell the person how to do that: in a
terminal, `cd PROJECT` and then `claude` (or `codex`); in an app, open the folder
PROJECT. Claude Code asks once whether to use the project's lungecuta server, and Codex
whether to trust the project: both times the answer is yes.

Until then, carry on here. Read `PROJECT/AGENTS.md` before you edit anything and follow
it, giving every command `--project PROJECT`.

## 4. Showing the person

Start `lungecuta serve --project PROJECT` in the background (it keeps running); it
opens the editor in the browser at http://127.0.0.1:8770. Tell the person what they can
do there: play with sound, scrub, drag cuts and keyframes, shape a speed ramp in the
graph editor, grade. What they change reaches you through `lungecuta changes`.

When a render is made and checked (the guide says how), show the file with `open -R FILE`.
"""


def skill_text() -> str:
    return SKILL.format(version=version("lungecuta"))


def skill_files(home: Path) -> dict[str, Path]:
    return {agent: home / place / "SKILL.md" for agent, place in PLACES.items()}


def current(home: Path) -> dict[str, bool]:
    """Per agent, whether its skill file holds this version's text."""
    text = skill_text()
    return {agent: file.is_file() and file.read_text() == text for agent, file in skill_files(home).items()}


def install_skill(home: Path) -> dict[str, str]:
    """Write the skill for each agent: per file, created, updated or unchanged."""
    text, written = skill_text(), {}
    for file in skill_files(home).values():
        state = "unchanged" if file.is_file() and file.read_text() == text else "updated" if file.exists() else "created"
        if state != "unchanged":
            file.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(file, text)
        written[str(file)] = state
    return written
