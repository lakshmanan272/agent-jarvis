"""File and folder operations, plus Windows search."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

# Spoken shorthands for the folders people actually name out loud.
KNOWN_DIRS = {
    "desktop": Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "home": Path.home(),
}


def resolve_dir(name: str) -> Path | None:
    name = (name or "").strip().lower()
    if name in KNOWN_DIRS:
        return KNOWN_DIRS[name]
    candidate = Path(name).expanduser()
    if candidate.is_dir():
        return candidate
    return None


def default_dir() -> Path:
    """Where "create folder reports" lands when no location is spoken.

    The Desktop, not the working directory -- Jarvis is normally launched from
    its own install folder or from a shortcut, and silently creating the user's
    files inside the application directory is never what they meant.
    """
    desktop = KNOWN_DIRS["desktop"]
    return desktop if desktop.is_dir() else Path.home()


@intent(
    r"^(?:create|make|new)\s+(?:a\s+)?folder\s+(?:called\s+|named\s+)?(?P<name>.+?)"
    r"(?:\s+(?:in|on)\s+(?P<where>.+))?$",
    name="create_folder",
    verbatim=True,
    priority=8,
    description="Create a folder",
    examples=("create folder reports on desktop",),
)
def do_create_folder(ctx, name: str = "", where: str | None = None, **_) -> ActionResult:
    parent = resolve_dir(where) if where else default_dir()
    if parent is None:
        return ActionResult.fail(f"Can't find {where}.")
    target = parent / name.strip()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ActionResult.fail("Couldn't create it.", str(exc))
    return ActionResult(ok=True, say=f"Created {name}.", detail=str(target))


@intent(
    r"^(?:create|make|new)\s+(?:a\s+)?file\s+(?:called\s+|named\s+)?(?P<name>[\w.\- ]+?)"
    r"(?:\s+(?:in|on)\s+(?P<where>.+))?$",
    name="create_file",
    verbatim=True,
    priority=8,
    description="Create an empty file",
)
def do_create_file(ctx, name: str = "", where: str | None = None, **_) -> ActionResult:
    parent = resolve_dir(where) if where else default_dir()
    if parent is None:
        return ActionResult.fail(f"Can't find {where}.")
    target = parent / name.strip().replace(" ", "_")
    try:
        target.touch(exist_ok=True)
    except OSError as exc:
        return ActionResult.fail("Couldn't create it.", str(exc))
    return ActionResult(ok=True, say=f"Created {target.name}.", detail=str(target))


@intent(
    r"^(?:open|show)\s+(?:the\s+)?(?P<name>desktop|downloads|documents|pictures|"
    r"music|videos|home)\s*(?:folder)?$",
    name="open_folder",
    priority=9,
    description="Open a known folder in Explorer",
)
def do_open_folder(ctx, name: str = "", **_) -> ActionResult:
    path = resolve_dir(name)
    if path is None:
        return ActionResult.fail(f"No folder called {name}.")
    os.startfile(path)
    return ActionResult(ok=True, say=f"Opening {name}.")


@intent(
    r"^(?:delete|remove|trash)\s+(?:the\s+)?(?:file|folder)\s+(?P<path>.+)$",
    name="delete_path",
    verbatim=True,
    priority=8,
    description="Delete a file or folder",
    destructive=True,
)
def do_delete_path(ctx, path: str = "", _confirmed: bool = False, **_) -> ActionResult:
    target = Path(path.strip()).expanduser()
    if not target.is_absolute():
        target = default_dir() / target
    if not target.exists():
        return ActionResult.fail(f"{target.name} isn't there.")
    if not _confirmed and ctx.config.control.confirm_destructive:
        return ActionResult(
            ok=True, needs_confirm=f"Delete {target.name}? Say yes or no."
        )
    try:
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    except OSError as exc:
        return ActionResult.fail("Couldn't delete it.", str(exc))
    return ActionResult(ok=True, say=f"Deleted {target.name}.")


@intent(
    r"^(?:find|search\s+for)\s+(?:the\s+)?(?:file|files|folder)s?\s+(?P<query>.+)$",
    name="search_files",
    verbatim=True,
    priority=9,
    description="Search files with Windows Search",
)
def do_search_files(ctx, query: str = "", **_) -> ActionResult:
    query = query.strip()
    if not query:
        return ActionResult.fail("Search for what?")
    subprocess.Popen(["explorer", f"search-ms:query={query}"], shell=False)
    return ActionResult(ok=True, say=f"Searching for {query}.")


@intent(
    r"^(?:open|start)\s+(?:the\s+)?(?:windows\s+)?search$",
    r"^search\s+(?:in\s+)?windows\s+(?:for\s+)?(?P<query>.+)$",
    name="windows_search",
    verbatim=True,
    priority=8,
    description="Open the Start-menu search, optionally with a query",
)
def do_windows_search(ctx, query: str | None = None, **_) -> ActionResult:
    act.hotkey("win", "s")
    if query:
        act.type_text(query, paste_threshold=ctx.config.control.paste_threshold)
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:copy|read)\s+(?:the\s+)?clipboard$",
    r"^what(?:'s|\s+is)\s+(?:in\s+)?(?:the\s+)?clipboard$",
    name="read_clipboard",
    priority=8,
    description="Read out the clipboard",
)
def do_read_clipboard(ctx, **_) -> ActionResult:
    text = act.read_clipboard().strip()
    if not text:
        return ActionResult(ok=True, say="Clipboard is empty.")
    preview = text if len(text) <= 160 else text[:157] + "..."
    return ActionResult(ok=True, say=preview, detail=text)


@intent(
    r"^(?:run|execute)\s+command\s+(?P<command>.+)$",
    name="run_command",
    verbatim=True,
    priority=9,
    description="Run a shell command in a visible terminal",
    destructive=True,
)
def do_run_command(ctx, command: str = "", _confirmed: bool = False, **_) -> ActionResult:
    command = command.strip()
    if not command:
        return ActionResult.fail("Run what?")
    if not _confirmed and ctx.config.control.confirm_destructive:
        return ActionResult(ok=True, needs_confirm=f"Run '{command}'? Say yes or no.")
    # /k keeps the window open so the user sees what happened.
    subprocess.Popen(["cmd", "/k", command], shell=False)
    return ActionResult(ok=True, say="Running it.", detail=command)
