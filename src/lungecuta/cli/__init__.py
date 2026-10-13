"""The `lc` command (docs/design/09-agent-interface.md): the group in `common.py`, its
commands by area in the modules beside it, which register themselves on the group when
they are imported."""
from . import deliver_commands, edit_commands, look_commands, project_commands
from .common import main

COMMAND_MODULES = (project_commands, look_commands, edit_commands, deliver_commands)

__all__ = ["main"]
