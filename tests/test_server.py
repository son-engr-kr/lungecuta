"""The editor's server on the comp in tests/clips.py, through FastAPI's test client."""
import json
import struct

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from lungecuta.cli import main
from lungecuta.server import create_app

from clips import TITLE, comp_json, put_comp


@pytest.fixture
def client(cut_project):
    (cut_project / "graphics" / "title.html").write_text(TITLE)
    comp = comp_json()
    comp["layers"].append({"id": "title", "type": "graphics", "page": "graphics/title.html", "space": "display",
                           "start": "b:2", "out": "b:6", "params": {"colour": "red"}})
    put_comp(cut_project, comp)
    with TestClient(create_app(cut_project)) as client:
        yield client


def test_the_timeline_holds_layers_beats_and_markers(client):
    project = client.get("/api/project").json()
    assert project["comps"] == ["main"] and project["graphics_origin"].startswith("http://127.0.0.1:")
    timeline = client.get("/api/comp/main").json()
    assert (timeline["frames"], timeline["preview"], timeline["rate"]) == (188, [64, 36], "30000/1001")
    layers = {layer["id"]: layer for layer in timeline["layers"]}
    assert (layers["shot1"]["first"], layers["shot1"]["end"], layers["shot1"]["source_frames"]) == (8, 68, [2, 61])
    assert layers["shot1"]["in_at"] == pytest.approx(0.25 * 30000 / 1001)
    assert layers["title"]["type"] == "graphics" and layers["title"]["page"] == "title.html"
    assert [b["downbeat"] for b in timeline["beats"][:5]] == [True, False, False, False, True]
    assert {m["name"] for m in timeline["markers"]} == {"drop", "hit"}


def test_a_frame_comes_over_the_socket_as_rgba(client):
    with client.websocket_connect("/ws") as socket:
        socket.send_text(json.dumps({"type": "frame", "id": 7, "comp": "main", "frame": 30}))
        data = socket.receive_bytes()
    length = struct.unpack("<I", data[:4])[0]
    header = json.loads(data[4:4 + length])
    assert (header["id"], header["frame"], header["width"], header["height"]) == (7, 30, 64, 36)
    assert len(data) - 4 - length == 64 * 36 * 4


def test_the_mixdown_is_a_wav(client):
    data = client.get("/api/comp/main/audio.wav").content
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def test_an_edit_is_applied_journalled_and_undoable(client, cut_project):
    timeline = client.get("/api/comp/main").json()
    response = client.post("/api/edit", json={"comp": "main", "op": "move",
                                              "args": {"layer_id": "shot2", "by": "b:1"}, "expect": timeline["hash"]})
    assert response.status_code == 200, response.text
    assert response.json()["changes"] == ["shot2 start moved b:4 to b:5"]
    stale = client.post("/api/edit", json={"comp": "main", "op": "move",
                                           "args": {"layer_id": "shot2", "by": "b:1"}, "expect": timeline["hash"]})
    assert stale.status_code == 409 and "changed since it was shown" in stale.json()["detail"]
    shot2 = lambda: next(l for l in json.loads((cut_project / "comps" / "main.json").read_text())["layers"]  # noqa: E731
                         if l["id"] == "shot2")
    assert shot2()["start"] == "b:5"
    assert client.post("/api/undo").status_code == 200 and shot2()["start"] == "b:4"
    assert client.post("/api/redo").status_code == 200 and shot2()["start"] == "b:5"
    kinds = [entry["kind"] for entry in client.get("/api/journal").json()["entries"]]
    assert kinds == ["edit", "undo", "redo"]
    # The agent sees what the person did, once.
    seen = CliRunner().invoke(main, ["changes", "--project", str(cut_project)])
    assert [e["kind"] for e in json.loads(seen.output)["entries"]] == ["edit", "undo", "redo"]
    again = CliRunner().invoke(main, ["changes", "--project", str(cut_project)])
    assert json.loads(again.output)["entries"] == []


def test_an_undo_does_not_overwrite_what_the_agent_wrote_since(client, cut_project):
    timeline = client.get("/api/comp/main").json()
    client.post("/api/edit", json={"comp": "main", "op": "move", "args": {"layer_id": "shot2", "by": "b:1"},
                                   "expect": timeline["hash"]})
    put_comp(cut_project, comp_json(end="b:13"))  # the agent rewrites the comp
    response = client.post("/api/undo")
    assert response.status_code == 409 and "would overwrite that change" in response.json()["detail"]


def test_the_graphics_origin_serves_only_the_projects_graphics_with_a_local_policy(client):
    import urllib.request

    origin = client.get("/api/project").json()["graphics_origin"]
    with urllib.request.urlopen(f"{origin}/title.html") as page:
        assert page.headers["Content-Security-Policy"] == "default-src 'self' 'unsafe-inline' data: blob:"
        assert b"renderAt" in page.read()
    with urllib.request.urlopen(f"{origin}/__lc_frame.html") as wrapper:
        assert b"postMessage" in wrapper.read()


def test_an_agents_edit_reaches_the_editor_as_it_happens(client, cut_project):
    from lungecuta import ops
    from lungecuta.media import Project

    client.get("/api/comp/main")  # the editor has read the comp
    with client.websocket_connect("/ws") as socket:
        ops.move(Project.load(cut_project), "main", "shot2", "b:1")  # as `lc op` would, from outside
        message = json.loads(socket.receive_text())
    assert message["type"] == "changed" and message["file"] == "comps/main.json"
    # The editor's own writes are not echoed back as changes from outside.
    timeline = client.get("/api/comp/main").json()
    assert next(l for l in timeline["layers"] if l["id"] == "shot2")["start"] == pytest.approx(
        2.75 * 30000 / 1001, abs=1e-6)


def test_a_layer_s_curves_hold_its_properties_on_every_frame(client, cut_project):
    timeline = client.get("/api/comp/main").json()
    edit = lambda op, args, expect: client.post("/api/edit", json={"comp": "main", "op": op, "args": args, "expect": expect})
    response = edit("key", {"layer_id": "shot1", "prop": "scale", "at": "b:1", "value": 1.5, "ease": "out"}, timeline["hash"])
    assert response.status_code == 200, response.text
    layers = {layer["id"]: layer for layer in response.json()["timeline"]["layers"]}
    assert layers["shot1"]["keys"] == [{"prop": "scale", "frame": pytest.approx(0.75 * 30000 / 1001), "value": [1.5, 1.5],
                                        "ease": "out", "at": "b:1"}]
    curves = client.get("/api/comp/main/layer/shot1/curves").json()
    assert (curves["first"], curves["end"], len(curves["source"])) == (8, 68, 60)
    assert int(curves["source"][0]) == 2 and curves["speed"][0] == pytest.approx(1.0)  # frame 8 shows source frame 2
    assert curves["scale"][-1] == [1.5, 1.5]
    shot2 = client.get("/api/comp/main/layer/shot2/curves").json()
    assert shot2["speed"][0] == pytest.approx(0.5)
    music = client.get("/api/comp/main/layer/music/curves").json()
    assert music["gain"][0] == 0 and music["keys"] == []
    assert client.get("/api/comp/main/layer/title/curves").status_code == 409


def test_the_editor_lists_the_project_s_styles(client, cut_project):
    (cut_project / "styles" / "dip.json").write_text(json.dumps(
        {"id": "dip", "name": "a dip", "anchor": "beat", "tracks": {"speed": [{"at": "b:0", "v": 0.5}]}}))
    assert [style["id"] for style in client.get("/api/styles").json()] == ["dip"]


def test_the_editor_s_scopes_are_lc_scope_s_on_the_proxy(client):
    numbers = client.get("/api/comp/main/scope", params={"frame": 30}).json()
    assert numbers["on"] == "proxy" and set(numbers["luma"]) == {"p1", "p50", "p99"}
    image = client.get("/api/comp/main/scope.png", params={"frame": 30}).content
    assert image[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_write_during_a_read_is_never_taken_for_the_version_read(cut_project, monkeypatch):
    """The preview hashes the comp before reading it and reads again until nothing moved:
    a write landing just after the read once left the old comp under the new hash."""
    import lungecuta.preview as preview_module
    from lungecuta.media import Project
    from lungecuta.preview import Preview

    original = preview_module.load_comp
    path = cut_project / "comps" / "main.json"
    newer = json.loads(path.read_text())
    newer["layers"][2]["start"] = "b:5"
    calls = []

    def load_then_written(project, name):
        comp = original(project, name)
        if not calls:
            path.write_text(json.dumps(newer))  # someone writes right after this read
        calls.append(name)
        return comp

    monkeypatch.setattr(preview_module, "load_comp", load_then_written)
    preview = Preview(Project.load(cut_project), "main")
    assert preview.comp.layers[2].start == "b:5" and not preview.stale() and len(calls) == 2
