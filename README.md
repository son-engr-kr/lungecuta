# lungecuta

A video editor for short, music-driven pieces shot in log, built so that an AI agent and
a person edit the same project. The agent writes the project as text; the person scrubs,
drags keyframes and grades in a browser UI. Both see the same frames, because one engine
renders the preview and the final.

It aims for the feel of DaVinci Resolve's colour pipeline joined to After Effects' layers
and keyframes: camera log in, a DaVinci Wide Gamut working space, correctly tagged
Rec.709 out; compositions, precomps, a graph editor, expressions and code-driven motion
graphics.

The name is a pun on Runge-Kutta. A speed ramp is a curve of playback speed over output
time, and the source frame on screen is its integral: a ramp is an ODE that only looks
like an editing feature.

**Status: Phase 0**, agent tools on top of DaVinci Resolve free. The tools exist; what
Phase 0 still needs is a person in Resolve
([docs/research/phase0-resolve-checks.md](docs/research/phase0-resolve-checks.md)) and a
real edit. Agents working on the repository start with [AGENTS.md](AGENTS.md). Start
with [docs/design/00-overview.md](docs/design/00-overview.md); measurements behind the
design are in [bench/RESULTS.md](bench/RESULTS.md).

## Getting started

lungecuta runs on a Mac with Apple Silicon. You need Claude Code or Codex, and you do not
need to program: open either one in any folder, paste this, and answer its questions.

```
Install https://github.com/son-engr-kr/lungecuta so I can edit videos with it.
```

It installs lungecuta as the steps below say (asking you to approve the commands that
download or write outside that folder), then asks for your footage and your song and
makes your first project. You watch, scrub and adjust the edit in the editor it opens in
your browser.

### Installing, for the agent

Run each step and check it before the next, and tell the person what each download is.

1. `uname -sm` prints `Darwin arm64`. If not, stop: lungecuta runs only on macOS on
   Apple Silicon.
2. `git --version`. On a Mac without Apple's command line developer tools this opens
   their installer instead: ask the person to click Install, wait until it has finished,
   and run it again.
3. `uv --version`. If uv is missing, install it with Astral's installer,
   `curl -LsSf https://astral.sh/uv/install.sh | sh`, which puts it in `~/.local/bin`.
4. Clone the repository to `~/.local/share/lungecuta` and, in that directory, put the
   `lungecuta` command on the PATH with the Python and the versions the repository pins
   (editable, so a `git pull` updates it):

   ```sh
   git clone https://github.com/son-engr-kr/lungecuta ~/.local/share/lungecuta
   cd ~/.local/share/lungecuta
   uv tool install --editable . --python 3.12 --constraints <(uv export --frozen --no-dev --no-emit-project --no-hashes)
   ```

5. `lungecuta setup` fetches, once, what lungecuta needs beside its Python packages (the
   pinned ffmpeg build, Playwright's headless Chromium and the beat model, each otherwise
   fetched on first use) and writes the lungecuta skill for Claude Code
   (`~/.claude/skills/lungecuta/`) and Codex (`~/.agents/skills/lungecuta/`).
6. `lungecuta doctor` prints `"ok": true`. A check that fails says what to run.
7. Read `~/.claude/skills/lungecuta/SKILL.md` (Codex's copy is the same file) and follow
   it: it starts the person's first project.

A command just installed into `~/.local/bin` may not be found by the shell that
installed it: call it by its full path (`~/.local/bin/uv`, `~/.local/bin/lungecuta`), and
run `uv tool update-shell` if a new terminal does not find it either.

To update: `git -C ~/.local/share/lungecuta pull`, the install command again in that
directory, then `lungecuta setup`. To work on lungecuta itself, run the same install
command in your own clone; [AGENTS.md](AGENTS.md) is for agents working on the repository.

## Working with Claude Code or Codex

A project is a directory made by `lungecuta init DIR`. Open Claude Code or Codex in it:
the project has the guide the agent reads (`AGENTS.md`, imported by `CLAUDE.md`), a
`.mcp.json` that starts `lungecuta mcp` for Claude Code and a `.codex/config.toml` that
starts it for Codex, so the agent offers every command as an MCP tool, with frames and
sheets returned as images. Claude Code asks once before it starts a project's server;
Codex reads the project's config once you trust the project when it asks.

Without MCP the agent runs `lungecuta` in a shell: every command prints one JSON object, and
`lungecuta commands` describes them all.

## Phase 0 tools

```
lungecuta setup                            fetch the pinned ffmpeg, the headless Chromium, the beat model;
                                           write the skill for Claude Code and Codex
lungecuta doctor                           whether this machine has what lungecuta needs, and what to run
lungecuta init DIR                         a new project, with AGENTS.md and CLAUDE.md for a coding agent
lungecuta guide [--write]                  that guide, printed or brought up to date in a project
lungecuta status                           what is in a project: media, comps, unseen edits, stale renders
lungecuta commands                         every command, its arguments and options, as JSON
lungecuta mcp                              every command as an MCP tool on stdio, pictures as images
lungecuta root NAME DIR                    where a media root is on this machine
lungecuta relink --root NAME DIR           find a root's moved files by size and hash
lungecuta import FILES --root NAME         media records: colour and time decisions with evidence,
                                           decoder check, proxy with its parity test; music gets a
                                           48 kHz WAV and a beat map (Beat This!)
lungecuta detect FILE                      the colour-space decision without importing
lungecuta new-comp NAME --music KEY ...    a new comp (size, rate, end), its music at 0s giving the beat grid
lungecuta still COMP --at b:16             a comp's frame as the render delivers it, PNG and numbers
lungecuta sheet COMP --every b:1           a contact sheet of a comp, each tile labelled with its beat and layers
lungecuta scope COMP --at b:16             waveform, parade, vectorscope and their numbers
lungecuta still --media KEY --at f:N       the same three for one imported clip
lungecuta framing COMP LAYER               the comp's window in a layer's source frame, and where its edge shows
lungecuta beats KEY                        the beat analysis again, applied only if no beat literal moves
lungecuta validate                         every project file, original and comp checked
lungecuta fcpxml COMP                      the comp as FCPXML 1.10 for Resolve's File > Import > Timeline
lungecuta render COMP --preset P           the comp rendered to a deliverable, with a version manifest
lungecuta verify FILE --comp COMP ...      the deliverable checked against its comp and preset
lungecuta flash FILE                       WCAG 2.2's general flash threshold on a render
lungecuta graphic PAGE ...                 a web page rendered to ProRes 4444 with alpha
lungecuta patch-card DIR ... / lungecuta patches  a card of known colours, and reading an export back at it
lungecuta op OP COMP ...                   an edit operation: place, graphic, sfx, move, trim, slip, slide,
                                           roll, ripple-delete, split, snap, key, unkey, move-key, set,
                                           remap, speed, style, unstyle, look, unlook, grade, masks,
                                           transition, untransition, sound, duck
lungecuta stages --comp COMP               the time the edit took by stage, from the log of every command
lungecuta changes                          what the editor wrote since the agent last asked, and what is on
                                           its screen: the playhead, the selection, the range marked with I and O
lungecuta audio-check COMP                 cut offsets to beats and loudness per half beat
lungecuta lint COMP                        the review rules a machine can check, as warnings
lungecuta diff COMP [BEFORE] [AFTER]       what changed between two revisions, in editing terms
lungecuta licence KEY --name ... --source  under what terms a music file may be used
lungecuta serve                            the editor in the browser
lungecuta schema --write                   the JSON Schema of the project files
```

Every command prints one JSON object; `lungecuta COMMAND --help` says what it does. It runs
on macOS on Apple Silicon, the one platform with a pinned ffmpeg build
([docs/design/08-deliver.md](docs/design/08-deliver.md#licences)); ffmpeg on the PATH is
not used.

## Layout

```
docs/design/     the design, one file per subject, plus decisions.md
docs/research/   primary-source records the design cites
bench/           scripts behind the measurements the design relies on
src/lungecuta/   the package and the lungecuta command
schema/          JSON Schema of the project files, generated by lungecuta schema --write
tests/           uv run pytest tests/ (uv run pytest -m media for sample media)
```

## Licence

MIT, see [LICENSE](LICENSE). Model weights are not part of the repository: a feature that
uses a model takes one whose code and weights are permissively licensed, and anything
under narrower terms is an opt-in download that shows them first
([docs/design/decisions.md](docs/design/decisions.md), D10).
