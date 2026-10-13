"""Shared test setup.

Tests that read sample media are marked `media` and run with `uv run pytest -m media`.
They read the directory named by LUNGECUTA_SAMPLES; which file plays each role (the
S-Log3 sample, the HLG sample) is set in `tests/samples.local.json`, which is gitignored,
so the committed tests name no sample. A missing variable or file is a failure, not a skip.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

SAMPLES_FILE = Path(__file__).with_name("samples.local.json")


@pytest.fixture(scope="session")
def samples() -> dict[str, dict[str, Any]]:
    base = os.environ.get("LUNGECUTA_SAMPLES")
    assert base, "media tests need LUNGECUTA_SAMPLES, the directory holding the sample media"
    assert SAMPLES_FILE.is_file(), f"media tests need {SAMPLES_FILE.name}, naming the sample for each role"
    roles = json.loads(SAMPLES_FILE.read_text())
    resolved = {}
    for role, entry in roles.items():
        path = Path(base).expanduser() / entry["path"]
        assert path.is_file(), f"the {role} sample {path} is missing"
        resolved[role] = {**entry, "path": path}
    return resolved


@pytest.fixture
def cut_project(tmp_path):
    """A project with music, a clip and comp `main` (tests/clips.py)."""
    from clips import cut_project as make

    return make(tmp_path)
