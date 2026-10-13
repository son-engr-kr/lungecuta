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
    rows = json.loads(page.evaluate("JSON.stringify(rows().map(l => [l.id, l.first, l.end]))"))
    k = [row[0] for row in rows].index(layer_id)
    box = page.locator("#timeline").bounding_box()
    zoom = page.evaluate("state.zoom")
    page.mouse.click(box["x"] + (rows[k][1] + 5) * zoom, box["y"] + 26 + 18 + k * 30 + 12)


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
    caught_up(page, "the keys written by lc op", curves=False)
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
