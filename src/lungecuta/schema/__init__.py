"""The project files' schemas: pydantic models, and the JSON Schema committed under `schema/`.

`lc schema --write` regenerates the committed files, and a test fails when they differ
from the models (docs/design/09-agent-interface.md#conventions-for-every-command).
"""
from __future__ import annotations

import json

from pydantic import BaseModel

from .comp import CompFile
from .media import MediaRecord
from .project import ColorSettings, ProjectFile, RootsFile
from .style import StyleFile

# file name under schema/ -> model
SCHEMAS: dict[str, type[BaseModel]] = {
    "project.schema.json": ProjectFile,
    "roots.schema.json": RootsFile,
    "media.schema.json": MediaRecord,
    "comp.schema.json": CompFile,
    "style.schema.json": StyleFile,
}


def schema_text(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n"


__all__ = ["SCHEMAS", "ColorSettings", "CompFile", "MediaRecord", "ProjectFile", "RootsFile", "StyleFile",
           "schema_text"]
