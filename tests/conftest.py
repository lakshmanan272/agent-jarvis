"""Test-wide safety net.

Jarvis exists to drive the real machine: its handlers shut Windows down, delete
files, launch processes and open browser tabs. A test that dispatches one of
those for real does exactly what it says -- during development a confirmation
test genuinely powered the workstation off mid-run.

So every side-effecting exit from this package is stubbed here, autouse, for the
whole suite. Tests assert on what *would* have been executed via the `side_effects`
fixture. A test that wants the real thing has to say so explicitly, in-test, and
there is currently no reason to.
"""
from __future__ import annotations

import os
import subprocess
import webbrowser

import pytest


class RecordedCall(tuple):
    """(kind, args, kwargs) with friendlier reprs in assertion output."""

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{self[0]} {self[1]!r}>"


@pytest.fixture(autouse=True)
def side_effects(monkeypatch):
    """Block every real system side effect and record the attempts instead."""
    calls: list[RecordedCall] = []

    def record(kind):
        def stub(*args, **kwargs):
            calls.append(RecordedCall((kind, args, kwargs)))
            return _FakeCompleted()
        return stub

    monkeypatch.setattr(subprocess, "run", record("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", record("subprocess.Popen"))
    monkeypatch.setattr(webbrowser, "open", record("webbrowser.open"))
    # os.startfile only exists on Windows; raising=False keeps the suite
    # importable on Linux CI.
    monkeypatch.setattr(os, "startfile", record("os.startfile"), raising=False)

    # Skills import these by value, so patching the module attribute alone
    # would miss them.
    from jarvis.skills import apps, files, system, web

    for module in (apps, files, system):
        monkeypatch.setattr(module, "subprocess", _FakeSubprocess(calls), raising=False)
    monkeypatch.setattr(web, "open_url", record("open_url"), raising=False)
    monkeypatch.setattr(apps, "launch", record("launch"), raising=False)

    return calls


class _FakeCompleted:
    returncode = 0
    stdout = b""
    stderr = b""

    def communicate(self, *_a, **_k):
        return b"", b""

    def wait(self, *_a, **_k):
        return 0


class _FakeSubprocess:
    """Stands in for the `subprocess` module inside a skill module."""

    DETACHED_PROCESS = 0

    def __init__(self, calls: list) -> None:
        self._calls = calls

    def run(self, *args, **kwargs):
        self._calls.append(RecordedCall(("subprocess.run", args, kwargs)))
        return _FakeCompleted()

    def Popen(self, *args, **kwargs):  # noqa: N802 - mirrors the real API
        self._calls.append(RecordedCall(("subprocess.Popen", args, kwargs)))
        return _FakeCompleted()


@pytest.fixture(autouse=True)
def no_real_input(monkeypatch):
    """Replace every mouse/keyboard primitive with a recording stub."""
    from jarvis.core import actuator

    calls: list[tuple[str, tuple, dict]] = []
    for name in (
        "click", "move", "move_relative", "drag_to", "scroll", "press",
        "hotkey", "type_text", "mouse_down", "mouse_up", "write_clipboard",
    ):
        monkeypatch.setattr(
            actuator, name, lambda *a, _n=name, **k: calls.append((_n, a, k))
        )
    monkeypatch.setattr(actuator, "screen_size", lambda: (1920, 1080))
    monkeypatch.setattr(actuator, "position", lambda: (0, 0))
    monkeypatch.setattr(actuator, "read_clipboard", lambda: "sample text")
    return calls


@pytest.fixture
def router():
    from jarvis.config import Config
    from jarvis.core.router import Router
    from jarvis.skills.base import Context

    ctx = Context(config=Config(), speak=lambda _t: None)
    r = Router(ctx)
    ctx.variables["router"] = r
    return r
