"""Photos: imported where they lie, shown by lungecuta's photo page as a graphics layer
(src/lungecuta/media.py, ops/timing.py, web/photo.html)."""
import io
import json
import urllib.request

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image, ImageCms

from lungecuta import ops
from lungecuta.cli import main
from lungecuta.media import MEDIA_URL, Project, import_file, load_record
from lungecuta.look import comp_still

from clips import put_comp

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()  # what Pickapicka embeds


def patches(width=128, height=72):
    """Four flat patches, so a still can be compared code for code."""
    image = np.zeros((height, width, 3), np.uint8)
    image[:height // 2, :width // 2] = (200, 40, 40)
    image[:height // 2, width // 2:] = (40, 200, 40)
    image[height // 2:, :width // 2] = (40, 40, 200)
    image[height // 2:, width // 2:] = (230, 230, 230)
    return image


def photo_project(cut_project, name="P001.png", **save):
    (cut_project / "photos").mkdir(exist_ok=True)
    path = cut_project / "photos" / name
    Image.fromarray(patches()).save(path, **save)
    import_file(Project.load(cut_project), path, root="project")
    put_comp(cut_project, {"id": "photos", "size": [128, 72], "rate": "30000/1001", "end": "2s", "layers": []})
    return path


def test_a_photo_is_imported_from_its_own_header(cut_project):
    exif = Image.Exif()
    exif[0x0112] = 6  # turned a quarter: shown 72x128
    exif.get_ifd(0x8769)[0x9003] = "2026:10:12 13:08:51"
    photo_project(cut_project, "P002.jpg", quality=95, icc_profile=SRGB, exif=exif.tobytes())
    image = load_record(Project.load(cut_project), "P002").image
    assert (image.format, image.size, image.orientation) == ("jpeg", (72, 128), 6)
    assert image.icc == "sRGB built-in" and image.captured == "2026:10:12 13:08:51"
    status = CliRunner().invoke(main, ["status", "--project", str(cut_project)], catch_exceptions=False)
    assert json.loads(status.output)["media"]["photos"][0]["key"] == "P002"


def test_a_raw_is_developed_first(cut_project):
    raw = cut_project / "photos" / "DSC00001.ARW"
    raw.parent.mkdir()
    raw.write_bytes(b"not decoded")
    with pytest.raises(AssertionError, match="develop it to a JPEG, PNG or TIFF first"):
        import_file(Project.load(cut_project), raw, root="project")


def test_a_placed_photo_shows_code_for_code_as_a_top_layer(cut_project):
    photo_project(cut_project)
    result = ops.place(Project.load(cut_project), "photos", "P001", "0s", size="own", length="1s")
    assert result["changes"] == ["graphics/photo.html placed at 0s as P001, 1s long, with fit, media"]
    layer = json.loads((cut_project / "comps" / "photos.json").read_text())["layers"][0]
    assert layer["type"] == "graphics" and layer["params"] == {"media": "P001", "fit": "none"}
    assert (cut_project / "graphics" / "photo.html").is_file()
    still = comp_still(Project.load(cut_project), "photos", "0.5s")
    shown = np.asarray(Image.open(still["png"]).convert("RGB"))
    assert np.array_equal(shown, patches())  # a top overlay's codes are the deliverable's (sRGB in, sRGB out)
    with pytest.raises(AssertionError, match="give how long it shows"):
        ops.place(Project.load(cut_project), "photos", "P001", "1s")


def test_a_photo_is_turned_upright_by_its_exif_orientation(cut_project):
    exif = Image.Exif()
    exif[0x0112] = 6  # the stored pixels are to be turned 90 degrees clockwise to be shown
    photo_project(cut_project, "P003.png", exif=exif.tobytes())
    ops.place(Project.load(cut_project), "photos", "P003", "0s", size="fit", length="1s")
    still = comp_still(Project.load(cut_project), "photos", "0.5s")
    shown = np.asarray(Image.open(still["png"]).convert("RGB")).astype(int)
    lit = np.flatnonzero(shown.max(axis=(0, 2)) > 20)  # 72x128 upright, fitted into 128x72: a pillar 40 wide
    assert 36 <= lit.size <= 44 and abs((lit[0] + lit[-1]) / 2 - 63.5) <= 1
    # turned clockwise, the stored top-left (red) is at the shown top right
    top_right = shown[5, lit[-1] - 3]
    assert top_right[0] > 150 and top_right[1] < 100


def test_the_editor_serves_a_photo_to_its_pages_and_nothing_else(cut_project):
    from fastapi.testclient import TestClient

    from lungecuta.server import create_app

    path = photo_project(cut_project)
    with TestClient(create_app(cut_project)) as client:
        origin = client.get("/api/project").json()["graphics_origin"]
        with urllib.request.urlopen(origin + MEDIA_URL + "P001") as response:
            assert response.read() == path.read_bytes()
        for key in ("C0001", "missing", "..%2Fproject"):  # footage, no record, out of media/
            with pytest.raises(urllib.error.HTTPError):
                urllib.request.urlopen(origin + MEDIA_URL + key)


def test_a_render_lists_its_photos_and_a_changed_photo_is_refused(cut_project):
    path = photo_project(cut_project)
    ops.place(Project.load(cut_project), "photos", "P001", "0s", size="own", length="1s")
    render = CliRunner().invoke(main, ["render", "photos", "--preset", "web-h264", "--project", str(cut_project)],
                                catch_exceptions=False)
    assert render.exit_code == 0, render.output
    manifest = json.loads((cut_project / "cache" / "lungecuta" / "render" / "photos.web-h264.mp4.manifest.json").read_text())
    assert [key for key, _ in manifest["media"]] == ["P001"]
    status = lambda: json.loads(CliRunner().invoke(main, ["status", "--project", str(cut_project)]).output)["renders"][0]
    assert status()["current"]
    Image.fromarray(patches()[::-1].copy()).save(path)  # another photo under the same name
    from lungecuta.media import photo_file
    with pytest.raises(AssertionError, match="has changed since it was imported"):
        photo_file(Project.load(cut_project), "P001")


def test_a_photo_is_looked_at_with_still_and_is_nothing_to_decide(cut_project):
    exif = Image.Exif()
    exif[0x0112] = 6
    photo_project(cut_project, "P004.png", exif=exif.tobytes())
    run = lambda *args: json.loads(CliRunner().invoke(main, [*args, "--project", str(cut_project)],
                                                      catch_exceptions=False).output)
    printed = run("still", "--media", "P004", "--at", "0s")
    shown = np.asarray(Image.open(printed["png"]).convert("RGB"))
    assert printed["size"] == [72, 128] and np.array_equal(shown, np.rot90(patches(), k=-1))  # upright
    assert "P004" not in run("validate")["undecided"]
