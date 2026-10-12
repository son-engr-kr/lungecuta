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

**Status: design.** Nothing is implemented yet. Agents working on the repository start
with [AGENTS.md](AGENTS.md). Start with
[docs/design/00-overview.md](docs/design/00-overview.md); measurements behind the design
are in [bench/RESULTS.md](bench/RESULTS.md).

## Layout

```
docs/design/     the design, one file per subject, plus decisions.md
docs/research/   primary-source records the design cites
bench/           scripts behind the measurements the design relies on
```
