import json

import pytest
from pydantic import ValidationError

from lungecuta.files import content_hash, replace_if_unchanged, write_new
from lungecuta.media import Project, import_file, set_root
from lungecuta.project import init_project
from lungecuta.schema.media import MediaPath


@pytest.fixture
def project(tmp_path):
    return init_project(tmp_path / "edit.lungecuta")


def test_root_lists_the_root_and_places_it_locally(project, tmp_path):
    footage = tmp_path / "shoots"
    footage.mkdir()
    set_root(project, "footage", footage)
    set_root(project, "footage", footage)  # again: listed once
    assert json.loads((project / "project.json").read_text())["media_roots"] == ["footage"]
    assert json.loads((project / "roots.local.json").read_text()) == {"footage": str(footage.resolve())}
    assert Project.load(project).root_path("footage") == footage.resolve()
    assert Project.load(project).root_path("project") == project


def test_a_root_must_be_listed_and_placed(project, tmp_path):
    with pytest.raises(AssertionError, match="not one of the project's media_roots"):
        Project.load(project).root_path("footage")
    with pytest.raises(AssertionError, match="is not a directory"):
        set_root(project, "footage", tmp_path / "missing")


def test_import_refuses_a_file_outside_its_root(project, tmp_path):
    footage, elsewhere = tmp_path / "shoots", tmp_path / "elsewhere"
    footage.mkdir()
    elsewhere.mkdir()
    (elsewhere / "C0001.MP4").write_bytes(b"")
    set_root(project, "footage", footage)
    with pytest.raises(AssertionError, match="is not under root 'footage'"):
        import_file(Project.load(project), elsewhere / "C0001.MP4", root="footage")


def test_import_refuses_a_key_taken_already(project, tmp_path):
    footage = tmp_path / "shoots"
    footage.mkdir()
    (footage / "C0001.MP4").write_bytes(b"")
    set_root(project, "footage", footage)
    (project / "media" / "C0001.json").write_text("{}")
    with pytest.raises(AssertionError, match="exists already"):
        import_file(Project.load(project), footage / "C0001.MP4", root="footage")


@pytest.mark.parametrize("rel", ["/abs/C0001.MP4", "../C0001.MP4", "a/../C0001.MP4", "a//C0001.MP4"])
def test_a_media_path_stays_inside_its_root(rel):
    with pytest.raises(ValidationError):
        MediaPath(root="footage", rel=rel)


def test_writes_refuse_to_overwrite_or_to_lose_a_change(tmp_path):
    target = tmp_path / "file.json"
    write_new(target, "{}\n")
    with pytest.raises(AssertionError, match="exists already"):
        write_new(target, "{}\n")
    read_hash = content_hash(target)
    target.write_text('{"edited": "by the UI"}\n')
    with pytest.raises(AssertionError, match="changed while lungecuta was working"):
        replace_if_unchanged(target, read_hash, "{}\n")
    assert json.loads(target.read_text()) == {"edited": "by the UI"}


def test_relink_finds_moved_files_by_size_and_hash(project, tmp_path):
    import os

    from lungecuta.media import relink
    from lungecuta.music import wav_bytes
    import numpy as np

    old, new = tmp_path / "old", tmp_path / "new"
    (old / "day1").mkdir(parents=True)
    tone = np.sin(np.arange(48000, dtype=np.float32) / 10)[:, None].astype(np.float32)
    (old / "day1" / "a.wav").write_bytes(wav_bytes(np.repeat(tone, 2, axis=1), 48000))
    set_root(project, "music", old)
    record = import_file(Project.load(project), old / "day1" / "a.wav", root="music")
    (new / "renamed").mkdir(parents=True)
    os.rename(old / "day1" / "a.wav", new / "renamed" / "take.wav")
    (new / "same-size-other.wav").write_bytes(b"\0" * record.stat.size)  # same size, other content
    result = relink(Project.load(project), "music", new)
    assert result["paths_changed"] == [{"media": "a", "from": "day1/a.wav", "to": "renamed/take.wav"}]
    reloaded = Project.load(project)
    assert reloaded.root_path("music") == new.resolve()
    assert json.loads((project / "media" / "a.json").read_text())["path"] == {"root": "music", "rel": "renamed/take.wav"}


def test_relink_changes_nothing_unless_every_file_is_found(project, tmp_path):
    import numpy as np

    from lungecuta.media import relink
    from lungecuta.music import wav_bytes

    old, elsewhere = tmp_path / "old", tmp_path / "elsewhere"
    old.mkdir()
    elsewhere.mkdir()
    tone = np.repeat(np.sin(np.arange(48000, dtype=np.float32) / 7)[:, None], 2, axis=1).astype(np.float32)
    (old / "a.wav").write_bytes(wav_bytes(tone, 48000))
    set_root(project, "music", old)
    import_file(Project.load(project), old / "a.wav", root="music")
    (old / "a.wav").unlink()
    with pytest.raises(AssertionError, match=r"not found under .* by size and hash: \['a'\]; nothing changed"):
        relink(Project.load(project), "music", elsewhere)
    assert Project.load(project).root_path("music") == old.resolve()


def test_files_import_several_at_once_in_order_and_keys_are_checked_first(project, tmp_path):
    from clips import make_clip
    from lungecuta.media import import_files

    footage = tmp_path / "shoots"
    (footage / "b").mkdir(parents=True)
    clips = [make_clip(footage / name, frames=4) for name in ("C0003.mov", "C0001.mov", "C0002.mov")]
    set_root(project, "footage", footage)
    records = import_files(Project.load(project), clips, root="footage")
    assert [record.key for record in records] == ["C0003", "C0001", "C0002"]
    assert all(record.color.status == "undecided" for record in records)  # no sidecar, no tags
    again = make_clip(footage / "b" / "C0001.mov", frames=4)
    with pytest.raises(AssertionError, match=r"share the keys \['C0001'\]"):
        import_files(Project.load(project), [footage / "C0001.mov", again], root="footage")
