"""The batched SendInput typing path.

No real keystrokes: these exercise event-array construction and the fallback
decision, which is where the logic lives. The actual injection is one ctypes
call whose only interesting failure mode (a partial send) is covered by
stubbing it.
"""
from __future__ import annotations

import pytest

from jarvis.core import fastinput

pytestmark = pytest.mark.skipif(
    not fastinput.AVAILABLE, reason="SendInput is Windows-only"
)


def utf16_units(text: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in text)


@pytest.mark.parametrize(
    "text",
    [
        "Hello World",
        "The quick brown fox jumps over the lazy dog",
        "café naïve",
        "காலை வணக்கம்",          # Tamil: the old VK path could not type this
        "\U0001F64F",             # outside the BMP -> surrogate pair
        "mixed ascii + தமிழ் + \U0001F680",
    ],
)
def test_one_press_and_release_per_code_unit(text):
    events = fastinput._events_for(text)
    assert len(events) == utf16_units(text) * 2


def test_astral_char_becomes_a_surrogate_pair():
    events = fastinput._events_for("\U0001F64F")
    scans = [e.ki.wScan for e in events]
    # press/release of the high surrogate, then of the low one
    assert scans == [0xD83D, 0xD83D, 0xDE4F, 0xDE4F]


def test_events_alternate_press_then_release():
    events = fastinput._events_for("ab")
    ups = [bool(e.ki.dwFlags & fastinput.KEYEVENTF_KEYUP) for e in events]
    assert ups == [False, True, False, True]


def test_every_event_is_a_unicode_keyboard_event():
    for event in fastinput._events_for("hi"):
        assert event.type == fastinput.INPUT_KEYBOARD
        assert event.ki.dwFlags & fastinput.KEYEVENTF_UNICODE
        # wVk must stay 0: a non-zero virtual key would make Windows consult the
        # keyboard layout, which is exactly what this path exists to avoid.
        assert event.ki.wVk == 0


def test_empty_text_is_a_no_op():
    assert fastinput.type_text("") is False


def test_long_text_is_chunked(monkeypatch):
    sent_batches = []

    def fake_send(count, array, size):
        sent_batches.append(count)
        return count

    monkeypatch.setattr(fastinput._user32, "SendInput", fake_send)
    text = "x" * 1000  # 2000 events
    assert fastinput.type_text(text) is True
    assert sum(sent_batches) == 2000
    assert max(sent_batches) <= fastinput._CHUNK_EVENTS


def test_partial_send_reports_failure(monkeypatch):
    """A blocked window must fall back rather than type half a sentence."""
    monkeypatch.setattr(fastinput._user32, "SendInput", lambda c, a, s: c - 1)
    assert fastinput.type_text("hello") is False


# --- how actuator.type_text chooses a route --------------------------------
# The clipboard leads because SendInput's Unicode packets are silently
# mistyped by apps that resolve them lazily (the Windows 11 Notepad turns the
# tail of a sentence into repeats of its last character). See type_text.


def test_words_go_through_the_clipboard(monkeypatch, real_actuator):
    from jarvis.core import actuator

    monkeypatch.setattr(
        actuator.fastinput,
        "type_text",
        lambda *_a, **_k: pytest.fail("multi-char text must not use SendInput"),
    )
    copied, pasted = [], []
    monkeypatch.setattr(actuator.pyperclip, "paste", lambda: "previous")
    monkeypatch.setattr(actuator.pyperclip, "copy", copied.append)
    monkeypatch.setattr(
        actuator.pyautogui, "hotkey", lambda *k, **_kw: pasted.append(k)
    )
    real_actuator["type_text"]("hello lakshmanan welcome")
    assert copied[0] == "hello lakshmanan welcome"
    assert pasted == [("ctrl", "v")]


def test_a_single_character_skips_the_clipboard(monkeypatch, real_actuator):
    """Typing one comma should not borrow the user's clipboard."""
    from jarvis.core import actuator

    sent = []
    monkeypatch.setattr(
        actuator.fastinput, "type_text", lambda t: sent.append(t) or True
    )
    monkeypatch.setattr(
        actuator.pyperclip,
        "copy",
        lambda _t: pytest.fail("a single character must not touch the clipboard"),
    )
    real_actuator["type_text"](",")
    assert sent == [","]


def test_falls_back_to_typing_when_the_clipboard_is_unavailable(
    monkeypatch, real_actuator
):
    from jarvis.core import actuator

    def no_clipboard(*_a, **_k):
        raise OSError("clipboard locked")

    monkeypatch.setattr(actuator.pyperclip, "copy", no_clipboard)
    monkeypatch.setattr(actuator.pyperclip, "paste", no_clipboard)
    monkeypatch.setattr(actuator.fastinput, "type_text", lambda _t: False)
    typed = []
    monkeypatch.setattr(actuator.pyautogui, "typewrite", lambda t, **k: typed.append(t))
    real_actuator["type_text"]("hello")
    assert typed == ["hello"]


def test_clipboard_is_handed_back(monkeypatch, real_actuator):
    from jarvis.core import actuator

    clipboard = {"value": "the user's own copy"}
    monkeypatch.setattr(actuator.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        actuator.pyperclip, "copy", lambda t: clipboard.update(value=t)
    )
    monkeypatch.setattr(actuator.pyautogui, "hotkey", lambda *a, **k: None)
    timers = []
    monkeypatch.setattr(
        actuator.threading, "Timer", lambda _d, fn: _FakeTimer(fn, timers)
    )
    real_actuator["type_text"]("pasted text")
    assert clipboard["value"] == "pasted text"
    timers[0]()  # the restore timer fires
    assert clipboard["value"] == "the user's own copy"


def test_restore_leaves_a_newer_copy_alone(monkeypatch):
    """If the user copied something while we held the clipboard, it is theirs."""
    from jarvis.core import actuator

    clipboard = {"value": "something the user copied after us"}
    monkeypatch.setattr(actuator.pyperclip, "paste", lambda: clipboard["value"])
    monkeypatch.setattr(
        actuator.pyperclip, "copy", lambda t: clipboard.update(value=t)
    )
    actuator._restore_clipboard("our old value", only_if="what we pasted")
    assert clipboard["value"] == "something the user copied after us"


class _FakeTimer:
    def __init__(self, fn, sink):
        self._fn = fn
        sink.append(fn)

    def start(self):
        pass
