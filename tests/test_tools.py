import hashlib
import io
import json
import os
import shutil
import zipfile

import pytest
from click.testing import CliRunner

from lungecuta import tools
from lungecuta.cli import main
from lungecuta.tools import Binary, Build, fetch, missing, tool, verify

PROGRAM = b"#!/bin/sh\necho pinned\n"


def zipped(name: str, data: bytes) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(name, data)
    return out.getvalue()


def build_of(archive: bytes, program: bytes = PROGRAM) -> Build:
    binary = Binary("ffmpeg", len(archive), hashlib.sha256(archive).hexdigest(),
                    len(program), hashlib.sha256(program).hexdigest())
    return Build("0.0", "darwin", "arm64", "https://builds.invalid/0.0/", (binary,))


def serving(archive: bytes, asked: list[str]):
    def opener(request, timeout):
        asked.append(request.full_url)
        return io.BytesIO(archive)
    return opener


def test_a_fetched_program_is_the_pinned_one_and_runs(tmp_path):
    archive, asked = zipped("ffmpeg", PROGRAM), []
    build = build_of(archive)
    assert missing(build, tmp_path) == list(build.binaries)
    assert fetch(build, tmp_path, opener=serving(archive, asked)) == ["ffmpeg"]
    assert asked == ["https://builds.invalid/0.0/ffmpeg.zip"]
    assert missing(build, tmp_path) == [] and verify(build, tmp_path) == {"ffmpeg": True}
    assert os.access(tmp_path / "ffmpeg", os.X_OK)
    assert fetch(build, tmp_path, opener=serving(archive, asked)) == [] and len(asked) == 1, "fetched once"
    assert sorted(p.name for p in tmp_path.iterdir()) == [".lock", "ffmpeg"], "no part files left behind"


def test_an_archive_that_is_not_the_pinned_one_is_refused(tmp_path):
    build = build_of(zipped("ffmpeg", PROGRAM))
    with pytest.raises(AssertionError, match="archive .* is not the pinned one"):
        fetch(build, tmp_path, opener=serving(zipped("ffmpeg", b"#!/bin/sh\necho other\n"), []))
    assert not (tmp_path / "ffmpeg").exists()


def test_a_program_that_is_not_the_pinned_one_is_refused(tmp_path):
    archive = zipped("ffmpeg", b"#!/bin/sh\necho other\n")
    build = build_of(archive)  # the archive as published, holding another program than pinned
    with pytest.raises(AssertionError, match="is not the pinned program"):
        fetch(build, tmp_path, opener=serving(archive, []))
    assert not (tmp_path / "ffmpeg").exists()


def test_a_changed_program_fails_verification(tmp_path):
    archive = zipped("ffmpeg", PROGRAM)
    build = build_of(archive)
    fetch(build, tmp_path, opener=serving(archive, []))
    (tmp_path / "ffmpeg").write_bytes(PROGRAM.replace(b"pinned", b"PINNED"))  # same size
    assert missing(build, tmp_path) == [] and verify(build, tmp_path) == {"ffmpeg": False}


def test_ffmpeg_is_the_pinned_build_and_git_comes_from_the_path():
    assert tool("ffmpeg") == str(tools.directory() / "ffmpeg")
    assert tool("ffprobe") == str(tools.directory() / "ffprobe")
    assert tool("git") == shutil.which("git")


def test_doctor_reports_each_requirement():
    result = CliRunner().invoke(main, ["doctor"], catch_exceptions=False)
    printed = json.loads(result.output)
    assert [check["name"] for check in printed["checks"]] == ["platform", "ffmpeg", "chromium", "beat model", "skill", "command"]
    assert printed["ok"] == all(check["ok"] for check in printed["checks"])
    assert result.exit_code == (0 if printed["ok"] else 1)
    assert all(("fix" in check) != check["ok"] for check in printed["checks"][1:])
