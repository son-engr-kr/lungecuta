"""`lungecuta mcp`: the commands as MCP tools over stdio, spoken to by the SDK's own client
(src/lungecuta/mcp_server.py)."""
import base64
import io
import json
import sys

import anyio
import click
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from PIL import Image

from lungecuta.cli import main
from lungecuta.mcp_server import argv, call, schema, tools

LAUNCH = "from lungecuta.cli import main; main()"


def test_every_command_is_a_tool_with_its_own_parameters():
    table = tools(main)
    assert {"still", "sheet", "op_move", "op_look", "status", "render"} <= set(table)
    assert not {"serve", "mcp", "commands"} & set(table)
    words, still = table["still"]
    properties = schema(still)["properties"]
    assert words == ["still"] and {"comp", "media", "at", "height", "project"} <= set(properties)
    assert schema(still)["required"] == ["at"] and properties["height"]["type"] == "integer"
    words, key = table["op_key"]
    assert words == ["op", "key"] and "position" in schema(key)["properties"]["prop"]["enum"]
    framing = schema(table["framing"][1])["properties"]["at"]
    assert framing["type"] == "array"  # --at may be given more than once
    assert argv(*table["framing"], {"comp": "main", "layer": "shot1", "at": ["b:1", "b:2"]}) == \
        ["framing", "main", "shot1", "--at", "b:1", "--at", "b:2"]


async def _session(project, steps):
    params = StdioServerParameters(command=sys.executable, args=["-c", LAUNCH, "mcp", "--project", str(project)])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as client:
            init = await client.initialize()
            return await steps(client, init)


def test_an_agent_sees_a_frame_and_edits_through_the_server(cut_project):
    async def steps(client, init):
        assert "lungecuta changes" in init.instructions and init.server_info.name == "lungecuta"
        names = {tool.name for tool in (await client.list_tools()).tools}
        still = await client.call_tool("still", {"comp": "main", "at": "b:4", "height": 36})
        moved = await client.call_tool("op_move", {"comp": "main", "layer": "shot2", "by": "f:1"})
        wrong = await client.call_tool("still", {"comp": "main", "at": "b:400"})
        return names, still, moved, wrong

    names, still, moved, wrong = anyio.run(_session, cut_project, steps)
    assert "op_move" in names
    assert not still.is_error
    printed = json.loads(still.content[0].text)
    assert printed["frame"] == 68 and printed["layers"] == ["shot2"]
    (image,) = [part for part in still.content if part.type == "image"]
    assert Image.open(io.BytesIO(base64.b64decode(image.data))).size == (64, 36)  # the frame, inline
    assert not moved.is_error and json.loads(moved.content[0].text)["changes"]
    assert json.loads((cut_project / "comps" / "main.json").read_text())["layers"][2]["start"] != "b:4"
    assert wrong.is_error and "outside the beat grid" in wrong.content[0].text  # an error, not a dead server


def test_a_command_that_exits_1_is_a_tool_error_with_its_report():
    """doctor, detect and beats exit through click (which returns the code when not
    standalone), verify and flash by SystemExit; either way the report comes back, marked
    as an error."""
    @click.group()
    def group():
        pass

    @group.command()
    @click.pass_context
    def through_click(context):
        click.echo('{"ok": false}')
        context.exit(1)

    @group.command()
    def through_system_exit():
        click.echo('{"ok": false}')
        raise SystemExit(1)

    @group.command()
    def passing():
        click.echo('{"ok": true}')

    for name, error in (("through-click", True), ("through-system-exit", True), ("passing", False)):
        result = call(group, [name], group.commands[name], {}, None)
        assert result.is_error == error and json.loads(result.content[0].text)["ok"] == (not error), name
