"""`lc mcp`: the lc commands as tools over the Model Context Protocol (stdio), for Claude
Code, Codex and any other MCP client (docs/design/09-agent-interface.md: "An MCP server, if
one is added, is a thin wrapper over the same commands").

Each command (the op group's operations each on their own) is a tool named for it
(`lc_still`, `lc_op_move`), its input schema built from the command's own arguments and
options, its description the command's help. A call runs the command in this process,
exactly as the CLI would, and returns what it prints; a frame, sheet, scope or framing
picture it writes comes back too, as an image the agent sees, no larger than 1568 pixels
on its long side (the size Anthropic's vision documentation says images are scaled down
to beyond; the file on disk keeps its own size). The server starts in a project (its
`--project`, or the directory it was started in), and every tool's `project` defaults to
it. The project guide (`lc guide`) is the server's instructions.

A command that fails returns its error and traceback as the tool's error instead of
ending the server: the protocol is the boundary where a failure becomes a message for the
caller, and nothing is retried or hidden.
"""
from __future__ import annotations

import base64
import inspect
import contextlib
import io
import json
import threading
import traceback
from importlib.metadata import version
from pathlib import Path
from typing import Any

import anyio
import click
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from PIL import Image

LONG_SIDE = 1568  # pixels: Anthropic's vision documentation scales a longer image down to this
SKIPPED = {"serve", "mcp", "commands"}  # a server that never returns, this one, and what the tool list replaces
READ_ONLY = {"still", "sheet", "scope", "framing", "status", "validate", "lint", "diff", "audio-check", "verify",
             "flash", "detect", "guide", "schema", "changes", "patches", "fcpxml"}
JSON_TYPES = {"integer": "integer", "float": "number", "boolean": "boolean"}


def _property(param: click.Parameter) -> dict[str, Any]:
    kind = param.type
    if isinstance(param, click.Option) and param.is_flag:
        schema: dict[str, Any] = {"type": "boolean"}
    elif isinstance(kind, click.Choice):
        schema = {"type": "string", "enum": list(kind.choices)}
    elif isinstance(kind, click.types.IntParamType):
        schema = {"type": "integer"}
    elif isinstance(kind, click.types.FloatParamType):
        schema = {"type": "number"}
    else:
        schema = {"type": JSON_TYPES.get(kind.name, "string")}
    many = (isinstance(param, click.Option) and param.multiple) or param.nargs == -1
    if many:
        schema = {"type": "array", "items": schema}
    help_text = getattr(param, "help", None)
    if help_text:
        schema["description"] = help_text
    default = param.default
    if isinstance(default, (str, int, float, bool)) and not many:
        schema["default"] = default
    return schema


def _key(param: click.Parameter) -> str:
    """The name a tool gives a parameter: an option's long flag without its dashes (`media`
    for `--media`, `project` for `--project`), an argument's own name."""
    if isinstance(param, click.Option):
        flag = max(param.opts, key=len)
        return flag.lstrip("-").replace("-", "_")
    return param.name


def tools(group: click.Group) -> dict[str, tuple[list[str], click.Command]]:
    """Every leaf command of the group by tool name, with the words that call it."""
    out: dict[str, tuple[list[str], click.Command]] = {}
    for name, command in sorted(group.commands.items()):
        if name in SKIPPED:
            continue
        if isinstance(command, click.Group):
            for sub, inner in sorted(command.commands.items()):
                out[f"lc_{name}_{sub}".replace("-", "_")] = ([name, sub], inner)
        else:
            out[f"lc_{name}".replace("-", "_")] = ([name], command)
    return out


def schema(command: click.Command) -> dict[str, Any]:
    properties, required = {}, []
    for param in command.params:
        if param.name == "help":
            continue
        properties[_key(param)] = _property(param)
        if param.required:
            required.append(_key(param))
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def argv(words: list[str], command: click.Command, arguments: dict[str, Any]) -> list[str]:
    """The command line a tool call stands for."""
    known = {_key(param): param for param in command.params if param.name != "help"}
    unknown = set(arguments) - set(known)
    assert not unknown, f"{' '.join(words)} has no parameter {', '.join(sorted(unknown))}"
    out = list(words)
    for name, param in known.items():
        if name not in arguments or arguments[name] is None:
            continue
        value = arguments[name]
        values = value if isinstance(value, list) else [value]
        if isinstance(param, click.Argument):
            out += [str(v) for v in values]
        elif param.is_flag:
            if value:
                out.append(max(param.opts, key=len))
        else:
            for v in values:
                out += [max(param.opts, key=len), json.dumps(v) if isinstance(v, (dict, list)) else str(v)]
    return out


def _image(path: Path) -> types.ImageContent:
    image = Image.open(path).convert("RGB")
    if max(image.size) > LONG_SIDE:
        factor = LONG_SIDE / max(image.size)
        image = image.resize((max(1, round(image.width * factor)), max(1, round(image.height * factor))),
                             Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return types.ImageContent(type="image", data=base64.b64encode(buffer.getvalue()).decode(), mime_type="image/png")


def pictures(printed: Any) -> list[Path]:
    """The PNGs a command says it wrote: `png`, or a sheet's `pages`."""
    if not isinstance(printed, dict):
        return []
    paths = [printed["png"]] if isinstance(printed.get("png"), str) else []
    paths += [page for page in printed.get("pages", []) if isinstance(page, str)]
    return [Path(path) for path in paths if path.endswith(".png") and Path(path).is_file()]


_ONE_AT_A_TIME = threading.Lock()  # a command's output is caught from the process's stdout


def call(group: click.Group, words: list[str], command: click.Command, arguments: dict[str, Any],
         project: Path | None) -> types.CallToolResult:
    """Run one command as the CLI would, and what it printed as the tool's result."""
    if project is not None and any(_key(param) == "project" for param in command.params):
        arguments = {"project": str(project)} | arguments
    args = argv(words, command, arguments)
    out = io.StringIO()
    failed, detail = False, ""
    with _ONE_AT_A_TIME, contextlib.redirect_stdout(out):
        try:
            group.main(args=args, prog_name="lc", standalone_mode=False)
        except SystemExit as stop:  # `lc verify` and `lc flash` print their report, then exit 1 when it fails
            failed = stop.code not in (0, None)
        except Exception:  # the boundary: the error becomes the tool's error, with its traceback
            failed, detail = True, traceback.format_exc()
    text = out.getvalue().strip()
    content: list = [types.TextContent(type="text", text=text or detail)]
    if text and detail:
        content.append(types.TextContent(type="text", text=detail))
    if text and not failed:
        printed = json.loads(text)
        content += [_image(path) for path in pictures(printed)]
    return types.CallToolResult(content=content, is_error=failed)


def server(group: click.Group, project: Path | None) -> Server:
    from .guide import guide_text

    table = tools(group)
    listed = [types.Tool(name=name, description=inspect.cleandoc(command.help or ""), input_schema=schema(command),
                         annotations=types.ToolAnnotations(read_only_hint=words[0] in READ_ONLY))
              for name, (words, command) in table.items()]

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=listed)

    async def call_tool(ctx, params) -> types.CallToolResult:
        assert params.name in table, f"no tool {params.name!r}"
        words, command = table[params.name]
        return await anyio.to_thread.run_sync(call, group, words, command, params.arguments or {}, project)

    return Server("lungecuta", version=version("lungecuta"), instructions=guide_text(),
                  on_list_tools=list_tools, on_call_tool=call_tool)


def run(group: click.Group, project: Path | None) -> None:
    app = server(group, project)

    async def main() -> None:
        async with stdio_server() as (read, write):
            await app.run(read, write, app.create_initialization_options())

    anyio.run(main)
