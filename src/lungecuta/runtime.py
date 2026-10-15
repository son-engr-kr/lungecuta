"""What lungecuta needs beside its Python packages, checked by `lungecuta doctor` and fetched
by `lungecuta setup`: the pinned ffmpeg build (`tools.py`), Playwright's headless Chromium,
which renders graphics, Beat This!'s checkpoint, which makes beat maps, the lungecuta skill
for Claude Code and Codex (`skill.py`), and the lungecuta command on the PATH, which a
project's MCP configuration starts. Each of the first three is also fetched on first use;
setup fetches them ahead, so that a first import or render does not stop to download."""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import gyroflow, tools
from .guide import PUT_ON_PATH, SERVER
from .skill import current, install_skill, skill_files

SETUP = "lungecuta setup"


def on_path(command: str) -> bool:
    """Whether `command` is on the PATH as a program started outside this process would
    see it: the bin directory of the virtual environment running lungecuta (`uv run` puts it
    first) does not count."""
    directories = os.environ.get("PATH", "").split(os.pathsep)
    if sys.prefix != sys.base_prefix:
        ours = (Path(sys.prefix) / "bin").resolve()
        directories = [d for d in directories if d and Path(d).resolve() != ours]
    return shutil.which(command, path=os.pathsep.join(directories)) is not None


def _playwright(*args: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    """Playwright's own command, from the environment lungecuta runs in."""
    return subprocess.run([sys.executable, "-m", "playwright", *args], text=True, check=True,
                          stdin=subprocess.DEVNULL, **kwargs)


def chromium_place() -> Path:
    """Where Playwright installs the headless shell `playwright.chromium.launch` starts, as
    its installer reports it."""
    out = _playwright("install", "--dry-run", "chromium-headless-shell", capture_output=True).stdout
    places = re.findall(r"Install location:\s+(.+)", out)
    assert places, f"playwright install --dry-run named no install location:\n{out}"
    return Path(places[0].strip())


def chromium_ready() -> bool:
    """Playwright marks a browser it finished installing with this file."""
    return (chromium_place() / "INSTALLATION_COMPLETE").is_file()


def checks() -> list[dict[str, Any]]:
    """Each requirement: whether it is met, what was found, and what to run if it is not."""
    from .music import CHECKPOINT, checkpoint_file

    out = []
    supported = tools.supported()
    out.append({"name": "platform", "ok": supported,
                "detail": f"{sys.platform} on {platform.machine()}; the pinned ffmpeg build is for "
                          f"{tools.BUILD.system} on {tools.BUILD.machine}"})
    if supported:
        verified = tools.verify()
        out.append({"name": "ffmpeg", "ok": all(verified.values()),
                    "detail": f"{tools.BUILD.version} from {tools.BUILD.source}, in {tools.directory()}: "
                              + ", ".join(f"{name} {'identical to the pinned one' if ok else 'missing or changed'}"
                                          for name, ok in verified.items()),
                    "fix": SETUP})
    place = chromium_place()
    out.append({"name": "chromium", "ok": chromium_ready(),
                "detail": f"Playwright's headless shell, for graphics, in {place}", "fix": SETUP})
    out.append({"name": "gyroflow", "ok": gyroflow.ready(),
                "detail": f"Gyroflow {gyroflow.RELEASE.version}, for lungecuta stabilize, in {gyroflow.directory()}",
                "fix": SETUP})
    checkpoint = checkpoint_file()
    out.append({"name": "beat model", "ok": checkpoint.is_file(),
                "detail": f"Beat This! checkpoint {CHECKPOINT}, for beat maps, in {checkpoint}", "fix": SETUP})
    installed = current(Path.home())
    out.append({"name": "skill", "ok": all(installed.values()),
                "detail": "the lungecuta skill, for starting a project: " + ", ".join(
                    f"{agent}'s in {file} {'current' if installed[agent] else 'missing or from another version'}"
                    for agent, file in skill_files(Path.home()).items()), "fix": SETUP})
    out.append({"name": "command", "ok": on_path(SERVER["command"]),
                "detail": f"{SERVER['command']} on the PATH, which a project's .mcp.json and "
                          ".codex/config.toml start", "fix": PUT_ON_PATH})
    for check in out:
        if check["ok"]:
            check.pop("fix", None)
    return out


def setup() -> dict[str, Any]:
    """Fetch what is missing: the pinned ffmpeg, the headless Chromium, Gyroflow, the beat model; and
    write the skill for Claude Code and Codex. Installers' progress goes to stderr, never
    stdout, which carries the JSON (and, under lungecuta mcp, the protocol)."""
    from .music import CHECKPOINT, checkpoint_file, fetch_checkpoint

    assert tools.supported(), (f"lungecuta pins an ffmpeg build for {tools.BUILD.system} on "
                               f"{tools.BUILD.machine} only, not {sys.platform} on {platform.machine()}")
    fetched = [f"{name} {tools.BUILD.version}" for name in tools.fetch()]
    if not chromium_ready():
        _playwright("install", "chromium-headless-shell", stdout=2)
        fetched.append("chromium headless shell")
    if gyroflow.fetch():
        fetched.append(f"Gyroflow {gyroflow.RELEASE.version}")
    if not checkpoint_file().is_file():
        fetch_checkpoint()
        fetched.append(f"Beat This! {CHECKPOINT}")
    return {"fetched": fetched, "skill": install_skill(Path.home())}
