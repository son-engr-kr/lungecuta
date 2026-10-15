"""lungecuta commands about the machine rather than a project: setup and doctor."""
from __future__ import annotations

import click

from .common import main, print_json


@main.command()
def setup() -> None:
    """Fetch what lungecuta needs beside its Python packages and is missing: the pinned
    ffmpeg build (checked by size and SHA-256), Playwright's headless Chromium for graphics,
    the pinned Gyroflow for lungecuta stabilize (checked the same way), and Beat This!'s
    checkpoint for beat maps. Each would otherwise be fetched on first use;
    this fetches them ahead. Prints what was fetched, then what lungecuta doctor finds."""
    from ..runtime import checks, setup as fetch_missing

    print_json(fetch_missing() | {"checks": checks()})


@main.command()
@click.pass_context
def doctor(context: click.Context) -> None:
    """Check this machine: the platform, the pinned ffmpeg build (hashed), the headless
    Chromium, the beat model, and the lungecuta command on the PATH. Each check says what it
    found and, if it failed, what to run. Exits with status 1 when one fails."""
    from ..runtime import checks

    found = checks()
    print_json({"ok": all(check["ok"] for check in found), "checks": found})
    if not all(check["ok"] for check in found):
        context.exit(1)
