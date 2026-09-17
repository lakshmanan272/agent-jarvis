"""Keystroke injection through a single Win32 `SendInput` call.

The problem this solves: every per-character API — pyautogui's `typewrite`,
`keybd_event`, AutoHotkey's default mode — pays a syscall and a scheduler slot
per key. At the 10 ms interval slow apps need, a 43-character sentence costs
430 ms. That is the difference between an assistant that feels instant and one
you wait for.

`SendInput` takes an *array* of events and injects the whole array atomically
into the input stream. One call, one syscall, every character. A sentence costs
microseconds instead of half a second, and because the events are atomic no
other process can interleave a keystroke into the middle of the text.

It also types via `KEYEVENTF_UNICODE`, which carries the character itself rather
than a virtual key code. That sidesteps keyboard layout entirely: on a French
AZERTY or a Tamil layout, a VK-based "a" is whatever key sits in that position,
while a Unicode event is always "a". Emoji and accented text work for free.
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger("jarvis.fastinput")

AVAILABLE = sys.platform == "win32"

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

# Windows accepts far larger arrays, but chunking keeps a single failed call
# from losing an entire paragraph and bounds the memory we hand to the kernel.
_CHUNK_EVENTS = 512

if AVAILABLE:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)

    ULONG_PTR = ctypes.POINTER(wintypes.ULONG) if ctypes.sizeof(
        ctypes.c_void_p
    ) == 4 else ctypes.c_ulonglong

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUTUNION(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("mi", _MOUSEINPUT), ("hi", _HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]

    _user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    _user32.SendInput.restype = wintypes.UINT


def _key_event(scan: int, key_up: bool) -> INPUT:
    event = INPUT(type=INPUT_KEYBOARD)
    event.ki = _KEYBDINPUT(
        wVk=0,
        wScan=scan,
        dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0),
        time=0,
        dwExtraInfo=0,
    )
    return event


def _events_for(text: str) -> list[INPUT]:
    """Two events (press, release) per UTF-16 code unit."""
    events: list[INPUT] = []
    for char in text:
        code = ord(char)
        if code > 0xFFFF:
            # Outside the BMP: Windows wants the surrogate pair, one event each.
            code -= 0x10000
            units = (0xD800 + (code >> 10), 0xDC00 + (code & 0x3FF))
        else:
            units = (code,)
        for unit in units:
            events.append(_key_event(unit, key_up=False))
            events.append(_key_event(unit, key_up=True))
    return events


def type_text(text: str) -> bool:
    """Inject `text` verbatim. Returns False if the platform/API refused it.

    A False return is the caller's cue to fall back; it never raises, because a
    failure here should degrade to slower typing rather than kill the command.
    """
    if not AVAILABLE or not text:
        return False

    events = _events_for(text)
    if not events:
        return False

    size = ctypes.sizeof(INPUT)
    for start in range(0, len(events), _CHUNK_EVENTS):
        chunk = events[start : start + _CHUNK_EVENTS]
        array = (INPUT * len(chunk))(*chunk)
        sent = _user32.SendInput(len(chunk), array, size)
        if sent != len(chunk):
            # Blocked by UIPI (a window running as administrator) or by the
            # secure desktop. Report it so the caller can try another route.
            log.warning(
                "SendInput delivered %d/%d events (error %d)",
                sent, len(chunk), ctypes.get_last_error(),
            )
            return False
    return True
