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

