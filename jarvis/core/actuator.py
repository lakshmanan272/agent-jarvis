"""The hands: every mouse and keyboard primitive funnels through here.

Two reasons for a single choke point. First, speed — pyautogui's defaults add a
0.1 s pause to *every* call and animate cursor moves, which is the difference
between a 90 ms command and a 900 ms one. Second, the panic stop: one flag here
halts anything in flight.
"""
from __future__ import annotations

import logging
import threading
import time

import pyautogui
import pyperclip

log = logging.getLogger("jarvis.actuator")

_ABORT = threading.Event()

KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "windows": "win", "start": "win",
    "command": "ctrl", "option": "alt", "return": "enter", "escape": "esc",
    "del": "delete", "pgup": "pageup", "pgdn": "pagedown", "caps": "capslock",
    "space bar": "space", "spacebar": "space", "arrow up": "up",
    "arrow down": "down", "arrow left": "left", "arrow right": "right",
    "print screen": "printscreen", "plus": "+", "minus": "-",
}


def configure(control_cfg) -> None:
    pyautogui.PAUSE = control_cfg.action_pause_s
    pyautogui.FAILSAFE = control_cfg.failsafe
    pyautogui.MINIMUM_DURATION = 0.0
    pyautogui.MINIMUM_SLEEP = 0.0
    log.debug("actuator configured: pause=%s failsafe=%s",
              pyautogui.PAUSE, pyautogui.FAILSAFE)


def abort() -> None:
    """Signal any in-flight multi-step action to unwind."""
    _ABORT.set()


def clear_abort() -> None:
    _ABORT.clear()


def aborted() -> bool:
    return _ABORT.is_set()


def _check() -> None:
    if _ABORT.is_set():
        raise InterruptedError("aborted by user")


# --- mouse -----------------------------------------------------------------

def screen_size() -> tuple[int, int]:
    return tuple(pyautogui.size())


def position() -> tuple[int, int]:
    return tuple(pyautogui.position())


def move(x: int, y: int, duration: float = 0.0) -> None:
    _check()
    pyautogui.moveTo(x, y, duration=duration, _pause=False)


def move_relative(dx: int, dy: int) -> None:
    _check()
    pyautogui.moveRel(dx, dy, duration=0.0, _pause=False)


def click(button: str = "left", clicks: int = 1, x=None, y=None) -> None:
    _check()
    pyautogui.click(x=x, y=y, clicks=clicks, button=button, interval=0.0, _pause=False)


def mouse_down(button: str = "left") -> None:
    _check()
    pyautogui.mouseDown(button=button, _pause=False)


def mouse_up(button: str = "left") -> None:
    _check()
    pyautogui.mouseUp(button=button, _pause=False)


def drag_to(x: int, y: int, duration: float = 0.25, button: str = "left") -> None:
    """Drags need a non-zero duration — instant drags get dropped by most apps."""
    _check()
    pyautogui.mouseDown(button=button, _pause=False)
    pyautogui.moveTo(x, y, duration=duration, _pause=False)
    pyautogui.mouseUp(button=button, _pause=False)


def scroll(amount: int, horizontal: bool = False) -> None:
    _check()
    if horizontal:
        pyautogui.hscroll(amount, _pause=False)
    else:
        pyautogui.scroll(amount, _pause=False)


# --- keyboard --------------------------------------------------------------

def normalize_key(key: str) -> str:
    key = key.strip().lower()
    return KEY_ALIASES.get(key, key)


def press(*keys: str, presses: int = 1) -> None:
    _check()
    for _ in range(presses):
        for key in keys:
            pyautogui.press(normalize_key(key), _pause=False)


def hotkey(*keys: str) -> None:
    _check()
    pyautogui.hotkey(*[normalize_key(k) for k in keys], _pause=False)


def type_text(text: str, paste_threshold: int = 24, interval: float = 0.0) -> None:
    """Type `text`, pasting long strings because typing them is O(n) keystrokes.

    Clipboard paste is effectively constant time and survives IME/layout quirks,
    so anything past the threshold goes through the clipboard and the previous
    clipboard contents are restored afterwards.
    """
    _check()
    if len(text) <= paste_threshold and text.isascii():
        pyautogui.typewrite(text, interval=interval, _pause=False)
        return
    try:
        saved = pyperclip.paste()
    except Exception:
        saved = None
    pyperclip.copy(text)
    time.sleep(0.02)  # give the clipboard a beat to settle before Ctrl+V
    pyautogui.hotkey("ctrl", "v", _pause=False)
    if saved is not None:
        threading.Timer(0.6, lambda: _restore_clipboard(saved)).start()


def _restore_clipboard(value: str) -> None:
    try:
        pyperclip.copy(value)
    except Exception:
        log.debug("could not restore clipboard", exc_info=True)


def read_clipboard() -> str:
    try:
        return pyperclip.paste()
    except Exception:
        return ""


def write_clipboard(text: str) -> None:
    pyperclip.copy(text)
