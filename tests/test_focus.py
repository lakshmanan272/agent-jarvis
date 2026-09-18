"""Giving the keyboard back before acting.

The bar has to take focus to offer a text box. If it still holds focus when the
command runs, "type hello" types into Jarvis's own box instead of the window the
user was working in — which is exactly what happened: the foreground window was
`Jarvis (pythonw.exe)` at the moment the paste fired.
"""
from __future__ import annotations

import pytest

from jarvis.core import focus


def test_our_own_windows_are_recognised():
    """A window we own must never be recorded as one to hand focus back to."""
    import os

    # The real check reads the window's owning pid; this asserts the contract
    # that it compares against *our* pid rather than anything else.
    assert focus.is_ours(None) is False
    assert os.getpid() > 0


def test_restore_ignores_a_window_that_is_gone(monkeypatch):
    monkeypatch.setattr(focus, "AVAILABLE", True)
    monkeypatch.setattr(focus._user32, "IsWindow", lambda _h: False)
    assert focus.restore(12345) is False


def test_restore_is_a_no_op_without_a_target():
    assert focus.restore(None) is False


def test_restore_waits_for_the_switch_to_land(monkeypatch):
    """SetForegroundWindow returns before focus moves; typing into that gap
    goes to the window we were leaving."""
    monkeypatch.setattr(focus, "AVAILABLE", True)
    monkeypatch.setattr(focus._user32, "IsWindow", lambda _h: True)
    monkeypatch.setattr(focus._user32, "IsIconic", lambda _h: False)
    monkeypatch.setattr(focus._user32, "SetForegroundWindow", lambda _h: 1)

    polls = {"n": 0}

    def slow_foreground():
        polls["n"] += 1
        return 999 if polls["n"] >= 3 else 111  # lands on the third poll

    monkeypatch.setattr(focus, "foreground", slow_foreground)
    assert focus.restore(999) is True
    assert polls["n"] >= 3


def test_restore_gives_up_rather_than_hanging(monkeypatch):
    monkeypatch.setattr(focus, "AVAILABLE", True)
    monkeypatch.setattr(focus._user32, "IsWindow", lambda _h: True)
    monkeypatch.setattr(focus._user32, "IsIconic", lambda _h: False)
    monkeypatch.setattr(focus._user32, "SetForegroundWindow", lambda _h: 1)
    monkeypatch.setattr(focus, "foreground", lambda: 111)  # never lands
    assert focus.restore(999, timeout=0.05) is False


def test_restore_reports_a_refusal(monkeypatch):
    monkeypatch.setattr(focus, "AVAILABLE", True)
    monkeypatch.setattr(focus._user32, "IsWindow", lambda _h: True)
    monkeypatch.setattr(focus._user32, "IsIconic", lambda _h: False)
    monkeypatch.setattr(focus._user32, "SetForegroundWindow", lambda _h: 0)
    assert focus.restore(999) is False


# --- the engine's hook -----------------------------------------------------


def _engine():
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    return Engine(cfg)


def test_hook_runs_before_the_command(no_real_input):
    engine = _engine()
    order: list[str] = []
    engine.before_dispatch = lambda: order.append("focus-handed-back")
    original = engine.router.dispatch

    def watched(raw, *a, **k):
        order.append("dispatch")
        return original(raw, *a, **k)

    engine.router.dispatch = watched
    engine.submit("select all", "text")
    assert order == ["focus-handed-back", "dispatch"]


def test_a_broken_hook_does_not_stop_the_command(no_real_input):
    engine = _engine()

    def boom():
        raise RuntimeError("focus restore blew up")

    engine.before_dispatch = boom
    result = engine.submit("select all", "text")
    assert result is not None and result.ok


def test_no_hook_is_fine(no_real_input):
    engine = _engine()
    assert engine.before_dispatch is None
    result = engine.submit("select all", "text")
    assert result is not None and result.ok


# --- focusing an existing window is asynchronous too ------------------------
# `activate()` returns before the switch lands. Reporting success at that point
# told a chain the window was ready, and "open notepad and write about X"
# composed two hundred words into whatever still held the keyboard.


def test_focus_window_waits_for_the_switch(monkeypatch):
    from jarvis.skills import window

    class FakeWindow:
        title = "Untitled - Notepad"
        isMinimized = False

        def activate(self):
            pass

    polls = {"n": 0}

    def foreground_after_a_moment():
        polls["n"] += 1
        return 7 if polls["n"] >= 3 else 1

    monkeypatch.setattr(focus, "foreground", foreground_after_a_moment)
    monkeypatch.setattr(
        focus, "title", lambda h: "Untitled - Notepad" if h == 7 else "Something Else"
    )
    assert window.focus_window(FakeWindow()) is True
    assert polls["n"] >= 3


def test_focus_window_reports_failure_rather_than_assuming(monkeypatch):
    from jarvis.skills import window

    class FakeWindow:
        title = "Untitled - Notepad"
        isMinimized = False

        def activate(self):
            pass

    monkeypatch.setattr(focus, "foreground", lambda: 1)
    monkeypatch.setattr(focus, "title", lambda _h: "Something Else")
    assert window.focus_window(FakeWindow(), wait=True) is False


def test_focus_window_can_skip_the_wait(monkeypatch):
    from jarvis.skills import window

    class FakeWindow:
        title = "x"
        isMinimized = False

        def activate(self):
            pass

    monkeypatch.setattr(
        focus, "foreground", lambda: pytest.fail("should not poll when wait=False")
    )
    assert window.focus_window(FakeWindow(), wait=False) is True


def test_opening_a_running_app_fails_when_focus_will_not_land(
    router, no_real_input, monkeypatch
):
    """Better to stop the chain than to type into the wrong application."""
    from jarvis.skills import apps

    class FakeWindow:
        title = "Untitled - Notepad"
        isMinimized = False

        def activate(self):
            pass

    monkeypatch.setattr(apps, "find_window", lambda _n: FakeWindow())
    monkeypatch.setattr(apps, "focus_window", lambda _w: False)

    result = router.dispatch("open notepad and select all")
    assert result.ok is False
    assert "forward" in result.say
    assert no_real_input == [], "the next step must not have run"



# --- a command must never act on Jarvis itself ------------------------------
# Every keystroke goes to whatever holds the keyboard. When that is the command
# bar, "select all and delete" runs perfectly against Jarvis's own empty text
# box: two steps, 8 ms, "Done, 2 steps." -- and the user's document untouched.
# That is worse than an error, because nothing about it looks wrong.


def _unstub(monkeypatch, real_actuator, *names):
    """Put the genuine actuator functions back for one test.

    `no_real_input` is autouse, so the module attributes are recording stubs
    everywhere. These tests are about what the real ones refuse to do.
    """
    from jarvis.core import actuator

    for name in names:
        monkeypatch.setattr(actuator, name, real_actuator[name])


def test_keystrokes_are_refused_when_jarvis_holds_the_keyboard(
    router, real_actuator, monkeypatch
):
    from jarvis.core import actuator

    _unstub(monkeypatch, real_actuator, "hotkey")
    monkeypatch.setattr(focus, "is_ours", lambda _h: True)
    sent: list = []
    monkeypatch.setattr(actuator.pyautogui, "hotkey", lambda *a, **k: sent.append(a))

    result = router.dispatch("select all")
    assert result.ok is False
    assert "keyboard" in result.say
    assert sent == [], "the keystroke went out anyway"


def test_keystrokes_go_out_normally_otherwise(router, real_actuator, monkeypatch):
    from jarvis.core import actuator

    _unstub(monkeypatch, real_actuator, "hotkey")
    monkeypatch.setattr(focus, "is_ours", lambda _h: False)
    sent: list = []
    monkeypatch.setattr(actuator.pyautogui, "hotkey", lambda *a, **k: sent.append(a))

    assert router.dispatch("select all").ok is True
    assert sent == [("ctrl", "a")]


def test_typing_is_refused_too(real_actuator, monkeypatch):
    from jarvis.core import actuator

    _unstub(monkeypatch, real_actuator, "type_text")
    monkeypatch.setattr(focus, "is_ours", lambda _h: True)
    with pytest.raises(actuator.WrongWindow):
        actuator.type_text("hello")


# --- remembering which window the user actually meant -----------------------
# Recording it when the bar opens is too late: clicking the orb makes the orb
# the foreground window, so there was nothing left to remember and the command
# ran with Jarvis holding the keyboard.


def test_the_last_real_window_is_remembered(monkeypatch):
    from jarvis.ui import hud as hud_module

    watcher = hud_module.HUD.__new__(hud_module.HUD)
    watcher._displaced = None

    monkeypatch.setattr(focus, "foreground", lambda: 4242)
    monkeypatch.setattr(focus, "is_ours", lambda h: False)
    watcher._remember_foreground()
    assert watcher._displaced == 4242

    # Our own orb taking focus must not overwrite it.
    monkeypatch.setattr(focus, "foreground", lambda: 99)
    monkeypatch.setattr(focus, "is_ours", lambda h: h == 99)
    watcher._remember_foreground()
    assert watcher._displaced == 4242, "the orb overwrote the user's window"
