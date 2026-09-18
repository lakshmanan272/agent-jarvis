"""Driving a VS Code-style editor from its command line.

Antigravity ships a CLI that takes the same arguments VS Code's does, which
makes it a far better target than clicking around its windows: opening a
project, jumping to a line, or diffing two files are one exec each and land
exactly where they were aimed. Nothing here goes through the screen, so none
of it depends on what is focused or what the OCR can read.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from jarvis.skills.base import ActionResult, intent
from jarvis.skills.files import resolve_dir

log = logging.getLogger("jarvis.editor")

# Where the installer puts it, in the order worth trying. The PATH lookup
# comes first so a user who installed it anywhere at all still works.
_FALLBACK_DIRS = (
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "antigravity" / "bin",
    Path("D:/Antigravity/bin"),
    Path("C:/Antigravity/bin"),
    Path(os.environ.get("PROGRAMFILES", "")) / "Antigravity" / "bin",
)

_cached_cli: str | None = None


def find_cli(configured: str = "") -> str | None:
    """Locate the editor's command line launcher.

    Resolved once and remembered: this runs on the path of every editor
    command, and stat-ing four directories to launch a window that opens in
    300 ms is time spent for nothing.
    """
    global _cached_cli
    if configured:
        return configured if Path(configured).exists() else None
    if _cached_cli is not None:
        return _cached_cli or None

    found = shutil.which("antigravity.cmd") or shutil.which("antigravity")
    if not found:
        for directory in _FALLBACK_DIRS:
            candidate = directory / "antigravity.cmd"
            if candidate.exists():
                found = str(candidate)
                break
    _cached_cli = found or ""
    log.info("editor cli: %s", found or "not found")
    return found


def _run(ctx, *args: str) -> ActionResult | None:
    """Invoke the CLI, or explain why it could not be. None means it ran."""
    cli = find_cli(ctx.config.editor.cli)
    if not cli:
        return ActionResult.fail(
            "I can't find Antigravity's command line. Set editor.cli in "
            "~/.jarvis/config.json to its path."
        )
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        # The launcher is a .cmd, so it needs a shell to interpret it; the
        # argument list is still passed as a list, never a joined string, so
        # a path with a space in it cannot be split apart.
        subprocess.Popen([cli, *args], shell=True, creationflags=detached)
    except OSError as exc:
        return ActionResult.fail("Antigravity wouldn't start.", str(exc))
    return None


def resolve_path(spoken: str) -> Path | None:
    r"""Turn what was said into a path on disk, or None.

    Tries it as a literal path, then as one of the folders people name out
    loud ("desktop"), then as a project name under the configured roots --
    so "open agent jarvis in antigravity" works without spelling out F:\.
    """
    spoken = (spoken or "").strip().strip('"').strip("'")
    if not spoken:
        return None

    direct = Path(spoken).expanduser()
    if direct.exists():
        return direct

    known = resolve_dir(spoken)
    if known is not None:
        return known

    from jarvis.config import Config

    wanted = spoken.lower().replace(" ", "")
    for root in Config.load().editor.project_roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for child in base.iterdir():
            if child.is_dir() and child.name.lower().replace(" ", "") == wanted:
                return child
    return None


@intent(
    r"^(?:open|start|launch)\s+antigravity$",
    r"^antigravity$",
    name="open_editor",
    priority=7,
    examples=("open antigravity",),
    description="Open Antigravity",
)
def do_open_editor(ctx, **_) -> ActionResult:
    failure = _run(ctx)
    return failure or ActionResult(ok=True, say="Opening Antigravity.")


@intent(
    r"^open\s+(?P<target>.+?)\s+in\s+antigravity$",
    r"^antigravity\s+open\s+(?P<target>.+)$",
    name="open_in_editor",
    priority=9,
    verbatim=True,
    examples=("open agent jarvis in antigravity",),
    description="Open a project or file in Antigravity",
)
def do_open_in_editor(ctx, target: str = "", **_) -> ActionResult:
    path = resolve_path(target)
    if path is None:
        return ActionResult.fail(
            f"I can't find {target}.",
            "not a path, a known folder, or a project under editor.project_roots",
        )
    # --reuse-window for a file, so reading one does not scatter windows;
    # a folder is a project and gets its own.
    flag = "--new-window" if path.is_dir() else "--reuse-window"
    failure = _run(ctx, flag, str(path))
    return failure or ActionResult(
        ok=True, say=f"Opening {path.name}.", detail=str(path)
    )


@intent(
    r"^(?:new|another)\s+antigravity\s+window$",
    name="new_editor_window",
    priority=8,
    examples=("new antigravity window",),
    description="Open an empty Antigravity window",
)
def do_new_editor_window(ctx, **_) -> ActionResult:
    failure = _run(ctx, "--new-window")
    return failure or ActionResult(ok=True, say="New window.")


@intent(
    r"^(?:compare|diff)\s+(?P<left>.+?)\s+(?:and|with|against)\s+(?P<right>.+?)"
    r"\s+in\s+antigravity$",
    name="editor_diff",
    priority=9,
    verbatim=True,
    examples=("compare old.py and new.py in antigravity",),
    description="Compare two files side by side in Antigravity",
)
def do_editor_diff(ctx, left: str = "", right: str = "", **_) -> ActionResult:
    first, second = resolve_path(left), resolve_path(right)
    missing = [
        spoken for spoken, path in ((left, first), (right, second)) if path is None
    ]
    if missing:
        return ActionResult.fail(f"I can't find {' or '.join(missing)}.")
    failure = _run(ctx, "--diff", str(first), str(second))
    return failure or ActionResult(
        ok=True, say="Comparing them.", detail=f"{first} <-> {second}"
    )


@intent(
    r"^(?:list\s+)?antigravity\s+extensions$",
    name="editor_extensions",
    priority=8,
    examples=("antigravity extensions",),
    description="List the extensions Antigravity has installed",
)
def do_editor_extensions(ctx, **_) -> ActionResult:
    cli = find_cli(ctx.config.editor.cli)
    if not cli:
        return ActionResult.fail("I can't find Antigravity's command line.")
    try:
        # Unlike the others this one is read and report, so it waits for the
        # answer rather than detaching.
        out = subprocess.run(
            [cli, "--list-extensions"], shell=True, capture_output=True,
            text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ActionResult.fail("Couldn't ask Antigravity.", str(exc))

    names = [line.strip() for line in out.splitlines() if line.strip()]
    if not names:
        return ActionResult(ok=True, say="No extensions installed.")
    return ActionResult(
        ok=True,
        say=f"{len(names)} extensions installed.",
        detail=", ".join(names),
        data={"extensions": names},
    )
