"""A page's events (05-motion-graphics.md, A page's events): a graphics layer's markers
reach its page as the `markers` query parameter, in exact layer seconds, and `lungecuta op
sfx --on markers --layer` puts sounds on them, on the comp in tests/clips.py (128x72 at
29.97 fps, beats every 0.5 s from 0.25 s)."""
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from clips import TITLE, comp_json, put_comp
from lungecuta.comp import load_comp, resolve
from lungecuta.graphics import page_query
from lungecuta.media import Project
from lungecuta.overlays import _key

from test_page_layers import page_alone
from test_sounds import run, sounding  # noqa: F401 (the fixture)

# Opaque white over the whole page from its marker named "in" on, nothing before.
EVENTS = """<!doctype html><html><head><style>html,body{margin:0;background:transparent}</style></head><body>
<canvas id="c" width="128" height="72"></canvas><script>
const markers = JSON.parse(new URLSearchParams(location.search).get("markers") || "[]");
const at = markers.find((m) => m.name === "in").t;
const ctx = document.getElementById("c").getContext("2d");
window.renderAt = (t) => { ctx.clearRect(0, 0, 128, 72); if (t >= at) { ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, 128, 72); } };
window.ready = true;
</script></body></html>"""

MARKERS = [{"t": "0.4s", "name": "in"}, {"t": "b:2", "name": "out"}]


def with_events(project_dir, markers=MARKERS, page=EVENTS):
    (project_dir / "graphics" / "page.html").write_text(page)
    comp = comp_json()
    comp["layers"].append({"id": "page", "type": "graphics", "page": "graphics/page.html", "space": "display",
                           "start": "b:2", "out": "b:6", "markers": markers})
    put_comp(project_dir, comp)
    project = Project.load(project_dir)
    return project, resolve(project, load_comp(project, "main"))


def test_the_page_query_holds_params_and_markers_only_when_given():
    assert page_query(None) == page_query({}) == ""
    query = parse_qs(urlsplit(page_query({"a": 1}, [{"name": "in", "t": 0.4}])).query)
    assert json.loads(query["params"][0]) == {"a": 1} and json.loads(query["markers"][0]) == [{"name": "in", "t": 0.4}]
    assert "params" not in parse_qs(urlsplit(page_query(None, [{"name": "in", "t": 0.4}])).query)


def test_markers_reach_the_page_in_exact_layer_seconds(cut_project):
    project, resolved = with_events(cut_project)
    (overlay,) = resolved.overlays
    assert overlay.markers == ({"name": "in", "t": 0.4}, {"name": "out", "t": 1.0})  # b:2 from b:2's start is 1 s
    # layer start b:2 is 1.25 s; the marker at 1.65 s of the comp lies between frames 49 and 50
    _, (_, before) = page_alone(project, resolved, 49)
    _, (_, after) = page_alone(project, resolved, 50)
    assert before.max() == 0 and after.min() == pytest.approx(1.0)


def test_only_a_layer_with_markers_is_keyed_by_them(cut_project):
    project, resolved = with_events(cut_project)
    (marked,) = resolved.overlays
    keyed = _key(project, marked, (128, 72), marked.resolution, {})
    project, resolved = with_events(cut_project, markers=[{"t": "0.5s", "name": "in"}])
    assert _key(project, resolved.overlays[0], (128, 72), marked.resolution, {}) != keyed, "a moved marker is a new capture"
    project, resolved = with_events(cut_project, markers=[], page=TITLE)
    import dataclasses
    plain = resolved.overlays[0]
    assert _key(project, plain, (128, 72), plain.resolution, {}) == _key(
        project, dataclasses.replace(plain, markers=()), (128, 72), plain.resolution, {})


def test_sfx_lands_on_a_page_s_events(sounding):  # noqa: F811
    with_events(sounding)
    comp = json.loads((sounding / "comps" / "main.json").read_text())
    put_comp(sounding, comp)
    placed = run(sounding, "op", "sfx", "main", "hit", "--on", "markers", "--layer", "page")
    # 1.65 s is frame 50 (the first at or after it); b:2 + 1 s = 2.25 s is frame 68 (67.43 rounded up)
    assert placed["changes"] == ["hit placed on f:50 as hit.1 by its rise", "hit placed on f:68 as hit.2 by its rise"]
    named = run(sounding, "op", "sfx", "main", "hit", "--on", "markers", "--layer", "page", "--named", "out")
    assert named["changes"] == ["hit placed on f:68 as hit.3 by its rise"]
    comp_own = run(sounding, "op", "sfx", "main", "hit", "--on", "markers", "--layer", "")
    assert comp_own["changes"] == ["hit placed on f:68 as hit.4 by its rise"]  # the comp's marker "drop" at b:4
    refused = run(sounding, "op", "sfx", "main", "hit", "--on", "beats", "--layer", "page", ok=False)
    assert "--layer and --named pick markers" in str(refused.exception)
