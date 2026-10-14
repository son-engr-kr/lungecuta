import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from lungecuta.cli import main
from lungecuta.project import PROJECT_DIRS, find_project, init_project, new_project
from lungecuta.schema import SCHEMAS, ProjectFile, RootsFile, schema_text

REPOSITORY = Path(__file__).resolve().parents[1]


def run(*args: str):
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_init_creates_the_project_layout(tmp_path):
    directory = tmp_path / "myedit.lungecuta"
    printed = run("init", str(directory))
    assert printed["project"] == str(directory.resolve())
    from lungecuta.runtime import on_path
    # .mcp.json starts `lungecuta mcp`, so init says when lungecuta is not on the PATH outside this venv
    assert ("mcp" in printed) == (not on_path("lungecuta"))

    for name in PROJECT_DIRS:
        assert (directory / name).is_dir()
        assert not any((directory / name).iterdir())
    assert json.loads((directory / "roots.local.json").read_text()) == {}
    assert (directory / ".gitignore").read_text() == "cache/\nroots.local.json\n"

    project = ProjectFile.model_validate_json((directory / "project.json").read_text())
    assert project == new_project()
    assert project.color.view == "ACES 2.0 - SDR 100 nits (Rec.709)"
    assert project.proxy_height == 540
    assert project.media_roots == [] and project.comps == [] and project.deliver == []


def test_init_accepts_an_existing_empty_directory(tmp_path):
    init_project(tmp_path)
    assert (tmp_path / "project.json").is_file()


def test_init_refuses_a_non_empty_directory(tmp_path):
    (tmp_path / "footage.mp4").write_bytes(b"")
    with pytest.raises(AssertionError, match="not empty"):
        init_project(tmp_path)
    assert not (tmp_path / "project.json").exists()


def test_init_refuses_a_file(tmp_path):
    target = tmp_path / "file"
    target.write_text("")
    with pytest.raises(AssertionError, match="not a directory"):
        init_project(target)


def test_the_project_is_found_from_any_directory_inside_it(tmp_path):
    root = init_project(tmp_path / "edit.lungecuta")
    assert find_project(root / "comps") == root
    assert find_project(root) == root
    with pytest.raises(AssertionError, match="no project.json"):
        find_project(tmp_path)


def test_project_files_state_every_field():
    with pytest.raises(ValidationError):
        ProjectFile.model_validate({"format": 1})
    with pytest.raises(ValidationError):
        ProjectFile.model_validate({**new_project().model_dump(), "extra": 1})


@pytest.mark.parametrize("roots", [["project"], ["footage", "footage"], ["Footage"], ["a/b"]])
def test_media_roots_are_plain_names_listed_once(roots):
    with pytest.raises(ValidationError):
        ProjectFile.model_validate({**new_project().model_dump(), "media_roots": roots})


def test_roots_file_places_roots_at_absolute_paths():
    assert RootsFile.model_validate({"footage": "/path/to/shoots"}).root == {"footage": "/path/to/shoots"}
    with pytest.raises(ValidationError, match="absolute path"):
        RootsFile.model_validate({"footage": "shoots"})
    with pytest.raises(ValidationError):
        RootsFile.model_validate({"project": "/path"})


@pytest.mark.parametrize("name", sorted(SCHEMAS))
def test_committed_schema_matches_the_models(name):
    committed = (REPOSITORY / "schema" / name).read_text()
    assert committed == schema_text(SCHEMAS[name]), "run `uv run lungecuta schema --write`"


def test_schema_writes_every_schema(tmp_path):
    printed = run("schema", "--write", "--dir", str(tmp_path))
    assert sorted(Path(path).name for path in printed["written"]) == sorted(SCHEMAS)
    assert set(run("schema")) == set(SCHEMAS)
