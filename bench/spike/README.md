# Phase 1 spike: the preview loop, measured

A throwaway prototype (`docs/design/10-roadmap.md`, Phase 1): enough of an engine, a server
and a viewer to measure the kill criteria an agent can measure. Nothing here is part of
the `lungecuta` package, and none of it is kept as the editor's code.

| script | measures | command |
| --- | --- | --- |
| `engine.py` | the preview chain for one comp frame at 540 lines: PyAV reads the FFV1 proxy, H.273 decode, input transform, a dynamic OCIO grade, a two-layer blend, output view, sRGB for the viewer | used by the others |
| `server.py`, `viewer.html` | an aiohttp WebSocket that renders a frame on request; a canvas that draws it and times to the next animation frame | used by `latency.py` |
| `latency.py` | a change to pixels on screen: a grade change on a parked frame, and a new frame each time (decode included) | `uv run --with aiohttp,av python bench/spike/latency.py PROJECT KEY` |
| `refill.py` | a timeline-wide change on a 60 s comp: time until every preview frame is rendered | `uv run --with aiohttp,av python bench/spike/refill.py PROJECT KEY` |
| `screen.py`, `title.html`, `overlay.html` | on-screen colour (a screenshot of the viewer against the frame sent) and graphics parity (a captured title composited against the same title live) | `uv run --with aiohttp,av python bench/spike/screen.py PROJECT KEY` |

PROJECT is a lungecuta project and KEY a media record in it with an FFV1 proxy. Results,
with machine and versions, are in `bench/RESULTS.md` ("Phase 1 spike").

Not measured here, because they need the person: colour against Resolve on a chart, and
data levels per camera body (a lens-cap shot and a chart from each). What reaches the
physical screen is not measured either: the screenshots are of Chromium's own surface.
