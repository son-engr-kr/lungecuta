"""The editor in the pinned Chromium on the comp in tests/clips.py: selecting a layer,
keying a property, grading, placing a style, remapping and dragging a remap key in the
graph, each checked in the comp file the engine wrote."""
import json
import socket
import threading
import time

import pytest
import uvicorn

from lungecuta.graphics import CHROMIUM_ARGS
from lungecuta.server import create_app

DIP = {"id": "dip", "name": "a dip", "anchor": "beat",
       "tracks": {"speed": [{"at": "b:-1", "v": 1.0}, {"at": "b:0", "v": 0.5}, {"at": "b:1", "v": 1.0}]}}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def page(cut_project):
    from playwright.sync_api import sync_playwright

    (cut_project / "styles" / "dip.json").write_text(json.dumps(DIP))
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(cut_project), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}/")
        page.wait_for_function("state.tl !== null")
        yield page
        assert errors == []
        browser.close()
    server.should_exit = True
    thread.join(5)


def shot1(project):
    return next(layer for layer in json.loads((project / "comps" / "main.json").read_text())["layers"]
                if layer["id"] == "shot1")


def click_layer(page, layer_id):
    first, row = json.loads(page.evaluate(
        f"JSON.stringify([state.tl.layers.find(l => l.id === {json.dumps(layer_id)}).first, rowOf({json.dumps(layer_id)})])"))
    box = page.locator("#timeline").bounding_box()
    zoom = page.evaluate("state.zoom")
    page.mouse.click(box["x"] + (first + 5) * zoom, box["y"] + 26 + 18 + row * 30 + 12)


SHOWN = """(async (curves) => {
  const tl = await (await fetch(`/api/comp/main`)).json();
  return state.tl.hash === tl.hash && (!curves || state.curves !== null);
})"""


def caught_up(page, what, curves=True):
    """Wait for the editor to show the comp as it is on disk (and the selected layer's
    curves, with `curves`). Polled from here: given an async function, wait_for_function
    takes the promise itself for a true value and does not wait."""
    deadline = time.monotonic() + 10
    while not page.evaluate(SHOWN, curves):
        assert time.monotonic() < deadline, f"{what}: the editor did not catch up; status {page.inner_text('#status')!r}"
        time.sleep(0.05)


def until(page, project, check, what):
    """Wait for the comp file to show an edit, then for the editor to have reloaded it."""
    deadline = time.monotonic() + 10
    while not check(shot1(project)):
        assert time.monotonic() < deadline, f"{what}: not written; status {page.inner_text('#status')!r}"
        time.sleep(0.05)
    caught_up(page, what)


def test_the_inspector_keys_grades_styles_and_remaps(page, cut_project):
    click_layer(page, "shot1")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('shot1')")
    page.evaluate("show(30)")

    # a scale key at the playhead, from the key button of the scale row
    page.locator("#inspector .row", has_text="scale").locator("button.key").click()
    until(page, cut_project, lambda layer: "transform" in layer, "the scale key")
    assert shot1(cut_project)["transform"]["scale"]["keys"][0]["v"] == 1.0  # a uniform scale is one number

    # contrast from the grade's number field
    field = page.locator("#inspector .row", has_text="contrast").locator("input[type=number]")
    field.fill("1.25")
    field.press("Enter")
    until(page, cut_project, lambda layer: "grade" in layer, "the grade")
    assert shot1(cut_project)["grade"] == [{"op": "primary", "contrast": 1.25}]

    # a style on the beat nearest the playhead, then taken off
    page.get_by_role("button", name="Place on beat").click()
    until(page, cut_project, lambda layer: "time" in layer, "the style")
    assert shot1(cut_project)["time"]["style"] == "dip"
    page.get_by_role("button", name="Remove style").click()
    until(page, cut_project, lambda layer: "time" not in layer, "taking the style off")

    # time remap, then the last remap key dragged down in the source graph
    page.get_by_role("button", name="Enable time remap").click()
    until(page, cut_project, lambda layer: "remap" in layer.get("time", {}), "the remap")
    keys = shot1(cut_project)["time"]["remap"]
    assert len(keys) == 2
    page.check("input[name=graph][value=source]")
    target = json.loads(page.evaluate("""JSON.stringify((() => {
      const key = state.curves.keys.filter(k => k.prop === 'remap')[1];
      return [graph.map.x(key.frame), graph.map.y(key.value * state.curves.media_fps)];
    })())"""))
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + target[0], box["y"] + target[1])
    page.mouse.down()
    page.mouse.move(box["x"] + target[0], box["y"] + target[1] + 40, steps=5)
    page.mouse.up()
    until(page, cut_project, lambda layer: layer["time"]["remap"][1]["v"] != keys[1]["v"], "the dragged key")
    after = shot1(cut_project)["time"]["remap"]
    assert after[1]["v"] != keys[1]["v"] and after[1]["v"].startswith("f:")
    page.wait_for_function("getComputedStyle(document.getElementById('missing')).display === 'none'", timeout=30000)


def test_the_inspector_sets_a_music_layer_s_gain_and_fade(page, cut_project):
    def music(project):
        return next(layer for layer in json.loads((project / "comps" / "main.json").read_text())["layers"]
                    if layer["id"] == "music")

    click_layer(page, "music")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('music')")
    field = page.locator("#inspector .row", has_text="gain").locator("input[type=number]")
    field.fill("-3")
    field.press("Enter")
    deadline = time.monotonic() + 10
    while music(cut_project).get("gain") != -3.0:
        assert time.monotonic() < deadline, page.inner_text("#status")
        time.sleep(0.05)
    page.wait_for_function("state.tl.layers.find(l => l.id === 'music').gain === -3")
    fade = page.locator("#inspector .row", has_text="fade out").locator("input[type=text]")
    fade.fill("1s")
    fade.press("Enter")
    while music(cut_project).get("fade_out") != "1s":
        assert time.monotonic() < deadline, page.inner_text("#status")
        time.sleep(0.05)


def test_the_inspector_puts_a_dissolve_on_the_cut(page, cut_project):
    from clips import comp_json, put_comp

    comp = comp_json()
    comp["layers"][2]["source_start"] = "f:10"  # shot2's handles
    put_comp(cut_project, comp)
    page.wait_for_function("state.tl.comp.layers[2].source_start === 'f:10'", timeout=10000)
    click_layer(page, "shot1")
    page.get_by_role("button", name="Dissolve").click()
    deadline = time.monotonic() + 10
    while "transitions" not in json.loads((cut_project / "comps" / "main.json").read_text()):
        assert time.monotonic() < deadline, page.inner_text("#status")
        time.sleep(0.05)
    page.wait_for_function("state.tl.layers.find(l => l.id === 'shot2').ramps.length === 1", timeout=10000)


def test_the_inspector_adds_and_edits_a_mask(page, cut_project):
    click_layer(page, "shot1")
    page.get_by_role("button", name="Add ellipse").click()
    until(page, cut_project, lambda layer: "masks" in layer, "the mask")
    assert shot1(cut_project)["masks"][0]["shape"] == "ellipse"
    field = page.locator("#inspector .row", has_text="feather").locator("input[type=number]")
    field.fill("0.1")
    field.press("Enter")
    until(page, cut_project, lambda layer: layer["masks"][0]["feather"] == 0.1, "the feather")


def test_the_scopes_panel_shows_the_frame_on_screen(page, cut_project):
    page.evaluate("show(30)")
    page.click("#scope-toggle")
    page.wait_for_function("document.getElementById('scope-image').naturalWidth > 0", timeout=20000)
    page.wait_for_function("document.getElementById('scope-numbers').textContent.startsWith('f:30')", timeout=20000)


def test_the_inspector_shows_a_node_graph(page, cut_project):
    from lungecuta import ops
    from lungecuta.media import Project

    ops.set_grade(Project.load(cut_project), "main", "shot1",
                  {"nodes": [{"ops": [{"op": "primary", "contrast": 1.2}]}, {"ops": [{"op": "cdl"}], "mix": 0.5}]})
    page.wait_for_function("!Array.isArray(state.tl.layers.find(l => l.id === 'shot1').grade)", timeout=10000)
    click_layer(page, "shot1")
    page.wait_for_function("document.getElementById('inspector').textContent.includes('node 2: cdl, mix 0.5')")


def test_a_position_key_is_dragged_in_the_graph(page, cut_project):
    from lungecuta import ops
    from lungecuta.media import Project

    project = Project.load(cut_project)
    ops.key(project, "main", "shot1", "position", "b:1", [0.5, 0.5])
    ops.key(project, "main", "shot1", "position", "b:3", [0.5, 0.5])
    caught_up(page, "the keys written by lungecuta op", curves=False)
    click_layer(page, "shot1")
    page.wait_for_function("state.curves !== null && state.curves.position !== undefined")
    modes = page.evaluate("[...document.querySelectorAll('input[name=graph]')].map(r => r.value)")
    assert modes == ["speed", "source", "x", "y", "scale", "opacity", "rotation"]
    page.check("input[name=graph][value=y]")
    target = json.loads(page.evaluate("""JSON.stringify((() => {
      const key = state.curves.keys.filter(k => k.prop === 'position')[1];
      return [graph.map.x(key.frame), graph.map.y(key.value[1])];
    })())"""))
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + target[0], box["y"] + target[1])
    page.mouse.down()
    page.mouse.move(box["x"] + target[0], box["y"] + target[1] + 30, steps=5)
    page.mouse.up()
    until(page, cut_project, lambda layer: layer["transform"]["position"]["keys"][1]["v"] != [0.5, 0.5],
          "the dragged position key")
    keys = shot1(cut_project)["transform"]["position"]["keys"]
    assert keys[0]["v"] == [0.5, 0.5] and keys[1]["v"][0] == 0.5 and keys[1]["v"][1] < 0.5  # only y, and down


def test_a_look_is_added_and_taken_off_in_the_inspector(page, cut_project):
    (cut_project / "styles" / "warm.json").write_text(json.dumps(
        {"id": "warm", "name": "warm", "grade": [{"op": "primary", "offset": 0.05}]}))
    page.wait_for_function("state.styles.some(s => s.id === 'warm')", timeout=10000)
    click_layer(page, "shot1")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('shot1')")
    assert page.evaluate("[...document.querySelectorAll('#inspector select option')].some(o => o.value === 'dip')")
    page.get_by_role("button", name="Add look").click()
    until(page, cut_project, lambda layer: "grade" in layer, "the look")
    assert shot1(cut_project)["grade"] == {"nodes": [{"look": "warm"}]}
    page.wait_for_function("document.getElementById('inspector').textContent.includes('node 1: look warm')")
    page.get_by_role("button", name="Remove").first.click()
    until(page, cut_project, lambda layer: "grade" not in layer, "taking the look off")


def test_the_inspector_opens_a_layer_s_shutter(page, cut_project):
    click_layer(page, "shot1")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('shot1')")
    field = page.locator("#inspector .row", has_text="shutter").locator("input[type=number]")
    field.fill("180")
    field.press("Enter")
    until(page, cut_project, lambda layer: "motion_blur" in layer, "the shutter")
    assert shot1(cut_project)["motion_blur"] == {"shutter_deg": 180.0}
    page.wait_for_function("[...document.querySelectorAll('input[name=graph]')].some(r => r.value === 'shutter')")



def test_a_key_dragged_across_the_graph_moves_in_time(page, cut_project):
    from lungecuta import ops
    from lungecuta.media import Project

    project = Project.load(cut_project)
    ops.key(project, "main", "shot1", "opacity", "b:1", 1.0)
    ops.key(project, "main", "shot1", "opacity", "b:3", 0.5)
    caught_up(page, "the keys written by lungecuta op", curves=False)
    click_layer(page, "shot1")
    page.wait_for_function("state.curves !== null && state.curves.opacity !== undefined")
    page.check("input[name=graph][value=opacity]")
    target = json.loads(page.evaluate("""JSON.stringify((() => {
      const key = state.curves.keys.filter(k => k.prop === 'opacity')[1];
      return [graph.map.x(key.frame), graph.map.y(key.value), key.frame, graph.map.x(key.frame - 10)];
    })())"""))
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + target[0], box["y"] + target[1])
    page.mouse.down()
    page.mouse.move(box["x"] + target[3], box["y"] + target[1], steps=6)
    page.mouse.up()
    before = ["b:1", "b:3"]
    until(page, cut_project, lambda layer: [k["t"] for k in layer["transform"]["opacity"]["keys"]] != before,
          "the key moved in time")
    keys = shot1(cut_project)["transform"]["opacity"]["keys"]
    assert keys[0] == {"t": "b:1", "v": 1.0} and keys[1]["v"] == 0.5 and keys[1]["t"] != "b:3"


def test_what_is_on_screen_reaches_the_agent(page, cut_project):
    from lungecuta.media import Project
    from lungecuta.view import read_view

    def reported(check, what):
        deadline = time.monotonic() + 10
        while not ((view := read_view(Project.load(cut_project))) and check(view)):
            assert time.monotonic() < deadline, f"{what}: not reported; status {page.inner_text('#status')!r}"
            time.sleep(0.05)
        return view

    page.evaluate("show(20)")
    page.keyboard.press("i")
    page.evaluate("show(90)")
    page.keyboard.press("o")
    click_layer(page, "shot2")
    view = reported(lambda v: v.get("selected") and v.get("marked", {}).get("out"), "the marks and the selection")
    assert view["comp"] == "main" and view["selected"] == {"layer": "shot2", "in_comp": True}
    assert (view["marked"]["from"], view["marked"]["to"]) == ("f:20", "f:91")
    assert view["playhead"]["frame"] == 90 and "shot2" in view["playhead"]["layers"]
    assert page.inner_text("#marks") == "marked f:20 to f:90"
    page.evaluate("show(10)")
    page.keyboard.press("o")  # an out before the in starts a new range
    view = reported(lambda v: v["marked"]["out"]["frame"] == 10, "a new range")
    assert view["marked"]["in"] is None and view["marked"]["from"] == "f:0"
    page.keyboard.press("Alt+KeyX")
    reported(lambda v: "marked" not in v, "clearing the marks")


def layer_of(project, layer_id):
    return next(layer for layer in json.loads((project / "comps" / "main.json").read_text())["layers"]
                if layer["id"] == layer_id)


def test_a_segment_of_the_speed_graph_is_dragged_to_another_speed(page, cut_project):
    click_layer(page, "shot2")  # half speed, not remapped
    page.wait_for_function("state.curves !== null && state.curves.speed !== undefined")
    page.check("input[name=graph][value=speed]")
    start, up = json.loads(page.evaluate("""JSON.stringify((() => {
      const c = state.curves, f = c.first + Math.floor((c.end - c.first) / 2);
      return [[graph.map.x(f), graph.map.y(c.speed[f - c.first] * 100)], graph.map.y(100) - graph.map.y(50)];
    })())"""))
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + start[0], box["y"] + start[1])
    page.mouse.down()
    page.mouse.move(box["x"] + start[0], box["y"] + start[1] + up, steps=8)
    page.mouse.up()
    deadline = time.monotonic() + 10
    while "remap" not in (layer_of(cut_project, "shot2").get("time") or {}):
        assert time.monotonic() < deadline, f"the dragged segment: not written; status {page.inner_text('#status')!r}"
        time.sleep(0.05)
    keys = layer_of(cut_project, "shot2")["time"]["remap"]
    assert [k["t"] for k in keys] == ["0s", "b:4"]  # the whole layer was one segment
    speed = (float(keys[1]["v"].rstrip("s")) - float(keys[0]["v"].rstrip("s"))) / 2.0  # four beats of 0.5 s
    assert speed == pytest.approx(1.0, abs=0.02)


def test_the_marked_range_is_set_to_a_speed_from_the_inspector(page, cut_project):
    click_layer(page, "shot2")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('shot2')")
    page.evaluate("show(83)")  # b:5 is 2.75 s, frame 82.42
    page.keyboard.press("i")
    page.evaluate("show(97)")  # to frame 97, before b:6 (3.25 s, frame 97.40)
    page.keyboard.press("o")
    row = page.locator("#inspector .actions", has=page.get_by_role("button", name="% over marked range"))
    row.locator("input[type=number]").fill("100")
    page.get_by_role("button", name="% over marked range").click()
    deadline = time.monotonic() + 10
    while "remap" not in (layer_of(cut_project, "shot2").get("time") or {}):
        assert time.monotonic() < deadline, f"the marked range: not written; status {page.inner_text('#status')!r}"
        time.sleep(0.05)
    keys = layer_of(cut_project, "shot2")["time"]["remap"]
    # shot2 starts on a beat, between frames, so its keys are written in beats (D15)
    assert len(keys) == 4 and all(k["t"].startswith("b:") for k in keys[1:3])
    beats = [float(k["t"].removeprefix("b:")) for k in keys[1:3]]
    source = [float(k["v"].rstrip("s")) for k in keys[1:3]]
    assert (source[1] - source[0]) / ((beats[1] - beats[0]) * 0.5) == pytest.approx(1.0, abs=1e-3)


def test_the_music_row_draws_its_waveform(page, cut_project):
    page.wait_for_function("state.peaks.hash === state.tl.hash")
    peaks = page.evaluate("state.peaks.layers.music.peaks")
    assert len(peaks) == page.evaluate("state.tl.frames") and max(peaks) > 0


def test_a_clip_s_own_sound_is_turned_on_in_the_inspector(page, cut_project):
    from clips import clip_with_sound, put_comp, write_record

    write_record(cut_project, clip_with_sound(cut_project / "footage" / "C0002.mov"))
    comp = json.loads((cut_project / "comps" / "main.json").read_text())
    comp["layers"].append({"id": "talk", "type": "footage", "media": "C0002", "start": "b:1", "out": "1.5s",
                           "source_start": "0.5s"})
    put_comp(cut_project, comp)
    page.reload()
    page.wait_for_function("state.tl !== null && state.tl.layers.some(l => l.id === 'talk')")
    click_layer(page, "talk")
    page.get_by_role("button", name="Play its sound").click()
    deadline = time.monotonic() + 10
    while "sound" not in layer_of(cut_project, "talk"):
        assert time.monotonic() < deadline, f"the sound: not written; status {page.inner_text('#status')!r}"
        time.sleep(0.05)
    page.wait_for_function("state.peaks.hash === state.tl.hash && state.peaks.layers.talk !== undefined")
    assert max(page.evaluate("state.peaks.layers.talk.peaks")) == 0.5  # the click, drawn on the footage row


def test_shots_that_never_overlap_share_a_track(page, cut_project):
    # shot1 and shot2 follow each other, so one track holds both; the music is below them
    rows = json.loads(page.evaluate("JSON.stringify(Object.fromEntries(state.tl.layers.map(l => [l.id, rowOf(l.id)])))"))
    assert rows == {"shot1": 0, "shot2": 0, "music": 1} and page.evaluate("layout().count") == 2
    from lungecuta import ops
    from lungecuta.media import Project

    ops.slip(Project.load(cut_project), "main", "shot2", "0.5s")  # source to dissolve from
    ops.transition(Project.load(cut_project), "main", "dissolve", ["shot1", "shot2"], "f:6")  # now they overlap
    caught_up(page, "the dissolve", curves=False)
    rows = json.loads(page.evaluate("JSON.stringify(Object.fromEntries(state.tl.layers.map(l => [l.id, rowOf(l.id)])))"))
    assert rows == {"shot2": 0, "shot1": 1, "music": 2}  # the later layer, drawn over, on the track above
    click_layer(page, "shot1")
    page.wait_for_function("state.selected === 'shot1'")


def test_a_graphics_layer_is_transformed_and_masked_in_the_inspector(page, cut_project):
    from clips import TITLE, comp_json, put_comp

    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].append({"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                           "start": "b:2", "out": "b:6", "params": {"colour": "rgb(200, 40, 120)"}})
    put_comp(cut_project, comp)
    page.wait_for_function("state.tl.layers.some(l => l.id === 'title') && state.graphics.has('title')")
    page.evaluate("show(60)")
    click_layer(page, "title")
    page.wait_for_function("document.querySelector('#inspector h2')?.textContent.startsWith('title')")
    title = lambda: json.loads((cut_project / "comps" / "main.json").read_text())["layers"][-1]
    deadline = time.monotonic() + 20
    for k, written in ((0, [0.5, 1.0]), (1, 0.5)):  # one field at a time: each change is an edit
        field = page.locator("#inspector .row", has_text="scale").locator("input[type=number]").nth(k)
        field.fill("0.5")
        field.press("Enter")
        while title().get("transform") != {"scale": written}:
            assert time.monotonic() < deadline, f"the scale: not written; status {page.inner_text('#status')!r}"
            time.sleep(0.05)
        caught_up(page, "the scale")
    page.wait_for_function("state.graphics.get('title').frame.style.transform.includes('scale(0.5, 0.5)')")
    page.get_by_role("button", name="Add ellipse").click()
    while "masks" not in title():
        assert time.monotonic() < deadline, "the mask: not written"
        time.sleep(0.05)
    caught_up(page, "the mask")
    page.evaluate("show(61)")
    page.wait_for_function("state.graphics.get('title').frame.style.maskImage.includes('mask.png')")
    mask = page.evaluate("""(async () => {
      const r = await fetch(`/api/comp/main/layer/title/mask.png`);
      return [r.status, r.headers.get('content-type')];
    })()""")
    assert mask == [200, "image/png"]
