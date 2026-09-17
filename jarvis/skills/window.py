"""Window management: focus, minimise, maximise, snap, move between monitors."""
from __future__ import annotations

import logging
import time

from jarvis.core import actuator as act
from jarvis.nlp.matcher import best_match
from jarvis.skills.base import ActionResult, intent

log = logging.getLogger("jarvis.skills.window")

try:
    import pygetwindow as gw
except ImportError:  # keeps the module importable for tests on non-Windows
    gw = None


def list_windows() -> list[str]:
    if gw is None:
        return []
    return [w for w in gw.getAllTitles() if w and w.strip()]


def find_window(name: str):
    """Best-effort fuzzy lookup of a visible window by its title."""
    if gw is None or not name:
        return None
    name = name.strip().lower()
    windows = [w for w in gw.getAllWindows() if w.title and w.title.strip()]
    if not windows:
        return None
    # Title -> window, lowercased, so the fuzzy matcher has clean keys.
    index = {w.title.lower(): w.title for w in windows}
    title = best_match(name, index)
    if title is None:
        return None
    for w in windows:
        if w.title == title:
            return w
    return None


FOCUS_SETTLE_S = 1.0


def focus_window(window, wait: bool = True) -> bool:
    """Raise a window and, by default, wait until it really has the keyboard.

    `activate()` returns before the switch has landed. Returning True at that
    point told a chain the window was ready, and the next step typed into
    whatever still held focus -- "open notepad and write about X" composed two
    hundred words into some other application. So the result is confirmed
    against the actual foreground window rather than assumed.
    """
    if window is None:
        return False
    try:
        if window.isMinimized:
            window.restore()
        window.activate()
    except Exception:
        # activate() throws when another process owns the foreground lock.
        # Minimise/restore asks the shell to do it instead, which usually works.
        try:
            window.minimize()
            window.restore()
        except Exception:
            log.debug("could not focus %r", getattr(window, "title", "?"), exc_info=True)
            return False

    if not wait:
        return True
    return _focus_landed(window)


def _focus_landed(window, timeout: float = FOCUS_SETTLE_S) -> bool:
    """Poll until `window` is the foreground window, or give up saying so."""
    from jarvis.core import focus as focus_api

    title = getattr(window, "title", "") or ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = focus_api.title(focus_api.foreground())
        if current and (current == title or title in current or current in title):
            return True
        time.sleep(0.02)
    log.info("focus did not settle on %r within %.0f ms", title, timeout * 1000)
    return False


def _active():
    if gw is None:
        return None
    try:
        return gw.getActiveWindow()
    except Exception:
        return None


@intent(
    r"^(?:minimi[sz]e|minimize)(?:\s+(?:the\s+)?(?P<app>.+?))?(?:\s+window)?$",
    name="minimize",
    priority=4,
    description="Minimise a window",
)
def do_minimize(ctx, app: str | None = None, **_) -> ActionResult:
    window = find_window(app) if app and app not in ("this", "it") else _active()
    if window is None:
        act.hotkey("win", "down")
        return ActionResult(ok=True, say="")
    window.minimize()
    return ActionResult(ok=True, say="")


@intent(
    r"^maximi[sz]e(?:\s+(?:the\s+)?(?P<app>.+?))?(?:\s+window)?$",
    r"^full\s*screen(?:\s+(?P<app>.+))?$",
    name="maximize",
    priority=4,
    description="Maximise a window",
)
def do_maximize(ctx, app: str | None = None, **_) -> ActionResult:
    window = find_window(app) if app and app not in ("this", "it") else _active()
    if window is None:
        act.hotkey("win", "up")
        return ActionResult(ok=True, say="")
    focus_window(window)
    window.maximize()
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:restore|un\s*maximi[sz]e)(?:\s+(?P<app>.+))?$",
    name="restore",
    description="Restore a window to its previous size",
)
def do_restore(ctx, app: str | None = None, **_) -> ActionResult:
    window = find_window(app) if app else _active()
    if window is None:
        return ActionResult.fail("No window there.")
    window.restore()
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:snap|dock|move)\s+(?:the\s+)?(?:window\s+)?(?:to\s+(?:the\s+)?)?"
    r"(?P<side>left|right)(?:\s+(?:half|side))?$",
    name="snap_window",
    examples=('snap to the left',),
    priority=5,
    description="Snap the active window to one half of the screen",
)
def do_snap(ctx, side: str = "left", **_) -> ActionResult:
    act.hotkey("win", side)
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:move|send)\s+(?:the\s+)?(?:window\s+)?to\s+(?:the\s+)?"
    r"(?:other|next|second)\s+(?:monitor|screen|display)$",
    name="move_monitor",
    examples=('move the window to the other monitor',),
    description="Throw the active window onto the next monitor",
)
def do_move_monitor(ctx, **_) -> ActionResult:
    act.hotkey("win", "shift", "right")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:minimi[sz]e\s+(?:all|everything)|show\s+(?:the\s+)?desktop|"
    r"clear\s+(?:the\s+)?screen)$",
    name="show_desktop",
    # Above open_folder: "show desktop" means minimise-all, not "open the
    # Desktop folder". "open desktop" still reaches open_folder.
    priority=10,
    description="Minimise everything",
)
def do_show_desktop(ctx, **_) -> ActionResult:
    act.hotkey("win", "d")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:switch|next|cycle)\s+window$",
    r"^alt\s+tab$",
    name="switch_window",
    priority=5,
    description="Switch to the next window",
)
def do_switch(ctx, **_) -> ActionResult:
    act.hotkey("alt", "tab")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:new|open)\s+(?:virtual\s+)?desktop$",
    name="new_desktop",
    description="Create a new virtual desktop",
)
def do_new_desktop(ctx, **_) -> ActionResult:
    act.hotkey("ctrl", "win", "d")
    return ActionResult(ok=True, say="New desktop.")


@intent(
    r"^(?:task\s+view|show\s+(?:all\s+)?windows)$",
    name="task_view",
    description="Open Task View",
)
def do_task_view(ctx, **_) -> ActionResult:
    act.hotkey("win", "tab")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:what\s+(?:is\s+)?(?:the\s+)?)?(?:active|current|focused)\s+window$",
    name="active_window",
    description="Report the focused window",
)
def do_active_window(ctx, **_) -> ActionResult:
    window = _active()
    if window is None or not window.title:
        return ActionResult(ok=True, say="Nothing focused.")
    return ActionResult(ok=True, say=window.title, data={"title": window.title})


def wait_for_window(name: str, timeout: float = 6.0, poll: float = 0.15):
    """Block until a window matching `name` appears. Used after cold launches."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        window = find_window(name)
        if window is not None:
            return window
        time.sleep(poll)
    return None
