"""Writing project files."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def json_text(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def write_atomic(path: Path, content: str | bytes) -> None:
    """Write through a temporary file in the same directory and a rename, so the server
    and the agent never read half a file."""
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if isinstance(content, bytes):
        temporary.write_bytes(content)
    else:
        temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def content_hash_text(text: str) -> str:
    """sha256 of a text, written as `content_hash` writes a file's."""
    return f"sha256:{hashlib.sha256(text.encode()).hexdigest()}"


def content_hash(path: Path) -> str:
    """sha256 of a file's bytes, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(8 << 20):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def write_new(path: Path, text: str) -> None:
    assert not path.exists(), f"{path} exists already; nothing written"
    write_atomic(path, text)


def replace_if_unchanged(path: Path, read_hash: str, text: str) -> None:
    """Write `path` only if it still has the content hash it had when it was read, the
    rule the UI's writes follow too (docs/design/01-architecture.md#files-edits-and-concurrency)."""
    assert content_hash(path) == read_hash, f"{path} changed while lungecuta was working on it; nothing written"
    write_atomic(path, text)
