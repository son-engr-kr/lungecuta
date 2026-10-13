"""The editor driven in the pinned Chromium: does it show frames, step by beats, play, and
write an edit made by dragging?

Usage: uv run python bench/editor_smoke.py PROJECT LAYER OUT_DIR
  (inside the project) PROJECT should be a copy: the drag writes to its first comp. LAYER
  is a footage layer of that comp to drag by about a beat.

Starts `lc serve`'s app on a free port, opens it headless, waits for the first frame,
presses Shift+Right twice, plays for two seconds, drags LAYER right by about the comp's
first beat interval, and prints the readouts, the status line, the journal and any page
errors, with screenshots before and after in OUT_DIR. Checked: the drag is journalled.
"""
import json
import socket
import sys
import threading
import time
from pathlib import Path

import uvicorn

from lungecuta.graphics import CHROMIUM_ARGS
from lungecuta.server import create_app


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def main() -> None:
    from playwright.sync_api import sync_playwright

    project, layer_id, out = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(project), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    visible = "getComputedStyle(document.getElementById('missing')).display === 'none'"
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=CHROMIUM_ARGS + ["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{port}/")
        page.wait_for_function("state.tl !== null")
        page.wait_for_function(visible, timeout=60000)
        page.screenshot(path=str(out / "editor_first.png"))
        page.keyboard.press("Shift+ArrowRight")
        page.keyboard.press("Shift+ArrowRight")
        print("after two beat steps:", page.inner_text("#time"), page.inner_text("#beat"))
        page.click("#play")
        time.sleep(2.0)
        page.click("#play")
        print("after 2 s of playback:", page.inner_text("#time"))
        rows = json.loads(page.evaluate("JSON.stringify(rows().map(l => [l.id, l.first, l.end]))"))
        zoom = page.evaluate("state.zoom")
        beat = page.evaluate("state.tl.beats.length > 1 ? state.tl.beats[1].frame - state.tl.beats[0].frame : 30")
        k = [row[0] for row in rows].index(layer_id)
        box = page.locator("#timeline").bounding_box()
        y = box["y"] + 26 + 18 + k * 30 + 15
        x = box["x"] + (rows[k][1] + rows[k][2]) / 2 * zoom
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + beat * zoom, y, steps=5)
        page.mouse.up()
        page.wait_for_function("document.getElementById('journal').children.length > 0", timeout=10000)
        page.evaluate(f"show({rows[k][1] + 5})")
        page.wait_for_function(visible, timeout=60000)
        print("status after the drag:", page.inner_text("#status"))
        print("journal:", page.inner_text("#journal"))
        page.screenshot(path=str(out / "editor_after.png"))
        print("page errors:", errors)
        browser.close()
    server.should_exit = True


if __name__ == "__main__":
    main()
