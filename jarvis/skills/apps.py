"""Launching, focusing and closing applications."""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from jarvis.config import DATA_DIR, USER_DIR
from jarvis.nlp.matcher import best_match
from jarvis.skills.base import ActionResult, intent
from jarvis.skills.window import find_window, focus_window

log = logging.getLogger("jarvis.skills.apps")

_ALIASES: dict[str, str] | None = None


def aliases() -> dict[str, str]:
    """Built-in aliases merged with the user's own overrides."""
    global _ALIASES
    if _ALIASES is not None:
        return _ALIASES
    merged: dict[str, str] = {}
    for path in (DATA_DIR / "app_aliases.json", USER_DIR / "app_aliases.json"):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            log.warning("could not read aliases from %s", path)
            continue
        merged.update({k: v for k, v in raw.items() if not k.startswith("_")})
    _ALIASES = merged
    return merged


def resolve_target(name: str) -> str | None:
    """Turn a spoken app name into something the shell can start."""
    name = name.strip().lower()
    if not name:
        return None
    hit = best_match(name, aliases())
    if hit:
        return hit
    # Unknown name: if it happens to be on PATH, run it verbatim.
    if shutil.which(name):
        return name
    return None


def _executable_on_path(exe: str) -> str | None:
    """Resolve `exe` to a real Windows executable, or None.

    shutil.which alone is not enough here: a POSIX-flavoured PATH (Git Bash,
    MSYS, Cygwin) puts extensionless shims like `usr/bin/notepad` ahead of the
    real thing, and exec'ing one raises WinError 193. Requiring a PATHEXT
    suffix filters those out and lets the shell fallback handle the name.
    """
    path = shutil.which(exe)
    if not path:
        return None
    suffixes = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD;.COM").lower().split(";")
    return path if Path(path).suffix.lower() in suffixes else None


def launch(target: str) -> bool:
    """Start `target` without blocking. Returns False if the shell refused it."""
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        if target.endswith(":") or "://" in target:
            os.startfile(target)  # protocol handler (ms-settings:, whatsapp:, ...)
            return True
        if Path(target).exists():
            os.startfile(target)
            return True

        exe, _, args = target.partition(" ")
        path = _executable_on_path(exe)
        if path:
            command = [path, *args.split()] if args else [path]
            subprocess.Popen(command, shell=False, creationflags=detached)
            return True

        # Not on PATH: hand it to the shell, which also consults the App Paths
        # registry key — that is how chrome, code and steam actually resolve.
        subprocess.Popen(
            ["cmd", "/c", "start", "", exe, *args.split()],
            shell=False,
            creationflags=detached,
        )
        return True
    except OSError as exc:
        log.warning("launch failed for %r: %s", target, exc)
        return False


@intent(
    r"^(?:open|launch|start|run|fire\s+up)\s+(?:the\s+)?(?:app\s+)?(?P<app>.+)$",
    name="open_app",
    priority=3,
    description="Open an application, folder or settings page",
    examples=("open chrome", "launch vscode", "open downloads"),
)
def do_open(ctx, app: str = "", **_) -> ActionResult:
    app = app.strip()
    if not app:
        return ActionResult.fail("Open what?")

    # Already running and visible? Focusing is faster than a cold start.
    window = find_window(app)
    if window is not None:
        focus_window(window)
        ctx.last_target = app
        return ActionResult(ok=True, say=f"{app} is up.")

    target = resolve_target(app)
    if target is None:
        return ActionResult.fail(
            f"I don't know {app}.",
            "Add it to ~/.jarvis/app_aliases.json to teach me.",
        )
    if not launch(target):
        return ActionResult.fail(f"Couldn't start {app}.")
    ctx.last_target = app
    return ActionResult(ok=True, say=f"Opening {app}.", data={"target": target})


@intent(
    r"^(?:switch|go|jump)\s+to\s+(?P<app>.+)$",
    r"^(?:focus|bring\s+up|show)\s+(?:the\s+)?(?P<app>.+?)(?:\s+window)?$",
    name="focus_app",
    priority=4,
    description="Bring an already-running window to the front",
    examples=("switch to chrome", "focus notepad"),
)
def do_focus(ctx, app: str = "", **_) -> ActionResult:
    window = find_window(app)
    if window is None:
        return do_open(ctx, app=app)
    focus_window(window)
    ctx.last_target = app
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:close|quit|exit|kill)\s+(?:the\s+)?(?P<app>.+?)(?:\s+(?:app|window))?$",
    name="close_app",
    priority=3,
    description="Close an application window",
    examples=("close chrome", "quit notepad"),
)
def do_close(ctx, app: str = "", **_) -> ActionResult:
    app = app.strip()
    if app in ("this", "that", "it", "window", "current window"):
        from jarvis.core import actuator as act

        act.hotkey("alt", "f4")
        return ActionResult(ok=True, say="Closed.")
    window = find_window(app)
    if window is None:
        return ActionResult.fail(f"{app} isn't open.")
    try:
        window.close()
    except Exception as exc:
        return ActionResult.fail(f"Couldn't close {app}.", str(exc))
    return ActionResult(ok=True, say=f"Closed {app}.")


@intent(
    r"^(?:restart|reopen)\s+(?P<app>.+)$",
    name="restart_app",
    description="Close an app and start it again",
)
def do_restart(ctx, app: str = "", **_) -> ActionResult:
    do_close(ctx, app=app)
    time.sleep(0.4)  # let the process release its window before relaunching
    return do_open(ctx, app=app)


@intent(
    r"^(?:what|which)\s+(?:apps?|programs?|windows?)\s+(?:are\s+)?"
    r"(?:open|running)$",
    name="list_apps",
    description="List open windows",
)
def do_list(ctx, **_) -> ActionResult:
    from jarvis.skills.window import list_windows

    titles = list_windows()
    if not titles:
        return ActionResult(ok=True, say="Nothing is open.")
    head = ", ".join(titles[:5])
    return ActionResult(
        ok=True,
        say=f"{len(titles)} windows. {head}.",
        detail="\n".join(titles),
        data={"windows": titles},
    )
