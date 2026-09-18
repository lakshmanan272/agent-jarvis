"""Driving Antigravity through its command line.

The CLI is a much better target than the editor's windows: every one of these
lands exactly where it was aimed regardless of what is focused, which is the
opposite of everything that goes through the screen.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def ran(monkeypatch, tmp_path):
    """Record the argument list instead of starting an editor."""
    from jarvis.skills import editor

    calls: list[list[str]] = []
    monkeypatch.setattr(
        editor.subprocess, "Popen", lambda args, **_k: calls.append(list(args))
    )
    monkeypatch.setattr(editor, "find_cli", lambda _c="": "C:/ag/antigravity.cmd")
    return calls


def _dispatch(phrase: str):
    from jarvis.config import Config
    from jarvis.core.router import Router
    from jarvis.skills.base import Context

    ctx = Context(config=Config(), speak=lambda _t: None)
    return Router(ctx).dispatch(phrase)


def test_opening_the_editor(ran):
    assert _dispatch("open antigravity").ok
    assert ran == [["C:/ag/antigravity.cmd"]]


def test_a_folder_opens_in_its_own_window(ran, tmp_path, monkeypatch):
    from jarvis.skills import editor

    project = tmp_path / "myproject"
    project.mkdir()
    monkeypatch.setattr(editor, "resolve_path", lambda _s: project)
    assert _dispatch("open myproject in antigravity").ok
    assert ran[0][1] == "--new-window"
    assert ran[0][2] == str(project)


def test_a_file_reuses_the_window(ran, tmp_path, monkeypatch):
    from jarvis.skills import editor

    note = tmp_path / "note.py"
    note.write_text("x = 1", encoding="utf-8")
    monkeypatch.setattr(editor, "resolve_path", lambda _s: note)
    assert _dispatch("open note.py in antigravity").ok
    assert ran[0][1] == "--reuse-window", "reading one file should not scatter windows"


def test_an_unknown_project_is_reported_not_guessed(ran):
    result = _dispatch("open nowhere at all in antigravity")
    assert result.ok is False
    assert ran == [], "nothing should have been launched"


def test_a_diff_passes_both_paths(ran, tmp_path, monkeypatch):
    from jarvis.skills import editor

    left, right = tmp_path / "a.py", tmp_path / "b.py"
    for f in (left, right):
        f.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        editor, "resolve_path", lambda s: left if "a.py" in s else right
    )
    assert _dispatch("compare a.py and b.py in antigravity").ok
    assert ran[0][1] == "--diff"
    assert ran[0][2:] == [str(left), str(right)]


def test_a_path_with_spaces_is_never_split(ran, tmp_path, monkeypatch):
    """The argument list is passed as a list, so a space is not a separator."""
    from jarvis.skills import editor

    project = tmp_path / "agent jarvis"
    project.mkdir()
    monkeypatch.setattr(editor, "resolve_path", lambda _s: project)
    _dispatch("open agent jarvis in antigravity")
    assert ran[0][-1] == str(project)
    assert len(ran[0]) == 3


def test_a_missing_cli_says_so_rather_than_failing_silently(monkeypatch):
    from jarvis.skills import editor

    monkeypatch.setattr(editor, "find_cli", lambda _c="": None)
    result = _dispatch("open antigravity")
    assert result.ok is False
    assert "editor.cli" in result.say


def test_a_project_is_found_by_name_under_a_configured_root(monkeypatch, tmp_path):
    """So the full path never has to be spoken aloud."""
    from jarvis.config import Config
    from jarvis.skills import editor

    (tmp_path / "Agent Jarvis").mkdir()
    cfg = Config()
    cfg.editor.project_roots = (str(tmp_path),)
    monkeypatch.setattr(Config, "load", classmethod(lambda _cls, *_a, **_k: cfg))
    assert editor.resolve_path("agent jarvis") == tmp_path / "Agent Jarvis"
    assert editor.resolve_path("no such project") is None
