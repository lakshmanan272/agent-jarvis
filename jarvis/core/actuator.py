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

from jarvis.core import fastinput

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


class WrongWindow(RuntimeError):
    """Jarvis holds the keyboard, so a keystroke would act on Jarvis."""


def _check_target() -> None:
    """Refuse to type into ourselves.

    Every keystroke goes to whatever has the keyboard, and when that is the
    command bar the command runs perfectly against Jarvis's own empty text
    box -- which is how "delete all text in the notepad" selected nothing,
    deleted nothing, and reported two steps done in 8 ms. Failing loudly is
    worth more than a fast, false success.
    """
    from jarvis.core import focus

    if focus.is_ours(focus.foreground()):
        raise WrongWindow(
            "Jarvis has the keyboard. Click the window you want first."
        )


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
    _check_target()
    _await_paste()
    for _ in range(presses):
        for key in keys:
            pyautogui.press(normalize_key(key), _pause=False)


def hotkey(*keys: str) -> None:
    _check()
    _check_target()
    _await_paste()
    pyautogui.hotkey(*[normalize_key(k) for k in keys], _pause=False)


# Below this length there is nothing for a lazy consumer to collapse, and
# borrowing the clipboard to insert one comma would be rude.
_KEYSTROKE_MAX = 1


def type_text(text: str, paste_threshold: int = 24, interval: float = 0.0) -> None:
    """Type `text` into the focused field, correctly and then quickly.

    The clipboard leads, which is not the obvious choice, so: injecting
    characters with `KEYEVENTF_UNICODE` sends each one as VK_PACKET, and some
    apps — including the Windows 11 Notepad — resolve queued packets against
    the *current* keyboard state rather than the state carried by each message.
    Send faster than they drain and the tail of the string collapses into
    repeats of the last character. Measured in Notepad, typing
    "hello lakshmanan welcome":

        clipboard paste            44 ms   correct
        SendInput  5 ms/char      207 ms   "hello lakshmanan eeeeeee"
        SendInput 10 ms/char      289 ms   "hello lakshmanan eelcome"
        SendInput 20 ms/char      540 ms   correct
        pyautogui typewrite       295 ms   correct

    So the clipboard is both the fastest route and the only one that is exact
    regardless of how the target app drains its input queue: the app receives
    one paste and reads the whole string itself. The batched-SendInput path is
    still far quicker where it works (a Tk entry takes the whole sentence in
    microseconds), but "works" is not something we can detect in advance, and a
    silently mistyped sentence is worse than 44 ms.
    """
    _check_target()
    _check()
    if not text:
        return

    # One character cannot be corrupted by a lazy resolve, and this keeps
    # "comma" or "space" instant without touching the clipboard.
    if len(text) <= _KEYSTROKE_MAX and fastinput.type_text(text):
        return

    if _paste(text):
        return

    log.debug("clipboard unavailable, falling back for %d chars", len(text))
    if fastinput.type_text(text):
        return
    pyautogui.typewrite(text, interval=interval or 0.01, _pause=False)


# Ctrl+V is asynchronous: the target reads the clipboard when it drains its
# message queue, not when the keys arrive. Overwrite the clipboard before then
# and the earlier paste yields the later text -- a six-step chain typed its last
# line three times. Measured against Notepad, back-to-back pastes need ~30 ms
# between them; this is double that for headroom, and it is only ever paid by
# the *next* paste, so a single command adds nothing.
PASTE_SETTLE_S = 0.06
_paste_lock = threading.Lock()
_last_paste_at = 0.0


def _await_paste() -> None:
    """Block until a recent paste has had time to be consumed.

    Anything that follows a paste has to wait for it, not just the next paste:
    a chain of "type ... / press enter / type ..." delivered Enter while the
    first paste was still queued, and the newline vanished. Costs nothing when
    no paste is outstanding.
    """
    remaining = PASTE_SETTLE_S - (time.monotonic() - _last_paste_at)
    if remaining > 0:
        time.sleep(remaining)


def _paste(text: str) -> bool:
    """Put `text` on the clipboard, paste it, and hand the clipboard back."""
    global _last_paste_at
    with _paste_lock:
        _await_paste()  # the previous paste must land before this one starts

        try:
            saved = pyperclip.paste()
        except Exception:
            saved = None
        try:
            pyperclip.copy(text)
        except Exception:
            log.debug("could not write the clipboard", exc_info=True)
            return False
        time.sleep(0.02)  # the clipboard write is asynchronous; let it land
        pyautogui.hotkey("ctrl", "v", _pause=False)
        _last_paste_at = time.monotonic()
    if saved is not None:
        # Late enough that the paste has certainly been read, soon enough that
        # the user's own clipboard is theirs again before they notice.
        threading.Timer(0.6, lambda: _restore_clipboard(saved, only_if=text)).start()
    return True


def _restore_clipboard(value: str, only_if: str | None = None) -> None:
    """Hand the clipboard back, unless something else has claimed it since.

    `only_if` is the text we pasted. If the clipboard no longer holds it, the
    user (or another app) copied something in the meantime, and restoring would
    destroy their copy instead of ours.
    """
    try:
        if only_if is not None and pyperclip.paste() != only_if:
            return
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
