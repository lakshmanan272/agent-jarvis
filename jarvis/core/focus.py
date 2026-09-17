"""Who has the keyboard.

Jarvis has to take focus to offer a text box and give it back before it acts.
Without the second half, "type hello" types into Jarvis's own command box: the
user clicks the orb, the entry takes focus to receive what they type, and the
foreground window is still Jarvis when the command runs.

So the HUD records the window it displaced and restores it before dispatching.
SetForegroundWindow is normally blocked by Windows' focus-stealing prevention,
but a process that *owns* the foreground window is explicitly allowed to hand it
away, which is exactly the position we are in.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import time

log = logging.getLogger("jarvis.focus")

AVAILABLE = sys.platform == "win32"

if AVAILABLE:
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.IsWindow.argtypes = (wintypes.HWND,)
    _user32.IsIconic.argtypes = (wintypes.HWND,)
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)

    SW_RESTORE = 9


def foreground() -> int | None:
    """The window that currently has the keyboard, or None."""
    if not AVAILABLE:
        return None
    hwnd = _user32.GetForegroundWindow()
    return int(hwnd) if hwnd else None


def title(hwnd: int | None) -> str:
    if not AVAILABLE or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(512)
    _user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def is_ours(hwnd: int | None) -> bool:
    """True when `hwnd` belongs to this process — the orb or the command bar."""
    if not AVAILABLE or not hwnd:
        return False
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == os.getpid()


def restore(hwnd: int | None, timeout: float = 0.25) -> bool:
    """Give the keyboard back to `hwnd`, and wait until it actually has it.

    The wait matters: `SetForegroundWindow` returns before the switch has
    landed, and typing into the gap goes to the window we were just leaving —
    which is the whole bug this exists to prevent. Polling costs a few
    milliseconds in the normal case and bounds the damage when focus is refused.
    """
    if not AVAILABLE or not hwnd:
        return False
    if not _user32.IsWindow(hwnd):
        return False
    if _user32.IsIconic(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)
    if not _user32.SetForegroundWindow(hwnd):
        log.debug("could not restore focus to %r", title(hwnd))
        return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if foreground() == hwnd:
            return True
        time.sleep(0.005)
    log.debug("focus did not settle on %r within %.0f ms", title(hwnd), timeout * 1000)
    return False


def our_window_rects() -> list[tuple[int, int, int, int]]:
    """Screen rectangles of this process's own visible windows.

    The orb and the command bar are on screen like anything else, so a search
    for text to click would happily find Jarvis's own status line and click
    that. These rectangles are the exclusion zone.
    """
    if not AVAILABLE:
        return []

    rects: list[tuple[int, int, int, int]] = []
    ours = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _lparam):
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == ours and _user32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            if _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                rects.append((rect.left, rect.top, rect.right, rect.bottom))
        return True

    _user32.EnumWindows(collect, 0)
    return rects
