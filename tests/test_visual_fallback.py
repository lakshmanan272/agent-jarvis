"""The last resort, when neither the tree nor the words can answer.

"select third option" on a File Explorer window showing three drives was
answered "I can't see third on screen". True, and no use: the word "third" is
written nowhere, and Explorer's drive tiles are not links, buttons or list
items to the accessibility tree. Nothing that reads text or counts controls
was ever going to get there, but it is plain to anyone looking at it.
"""
from __future__ import annotations

import json

import pytest

from jarvis.config import Config
from jarvis.core import screen
from jarvis.core.screen import Target
from jarvis.skills.base import Context


@pytest.fixture
def looking(monkeypatch):
    """A fake screen and a fake model; records what the model was asked."""
    asked: dict = {}

    class FakeShot:
        width, height = 1280, 720

        def thumbnail(self, _size):
            pass

        def save(self, buffer, format=None):
            buffer.write(b"png")

    monkeypatch.setattr(
        screen, "_screen_size", lambda: (2560, 1440), raising=False
    )

    class FakeAutogui:
        @staticmethod
        def screenshot():
            shot = FakeShot()
            shot.width = 2560  # full-size capture, scaled down to 1280 below
            return shot

    def install(reply, *, full_width=2560, view_width=1280):
        class Shot:
            width, height = full_width, int(full_width * 9 / 16)

            def thumbnail(self, _size):
                Shot.width, Shot.height = view_width, int(view_width * 9 / 16)
                self.width, self.height = Shot.width, Shot.height

            def save(self, buffer, format=None):
                buffer.write(b"png")

        import sys
        import types

        module = types.ModuleType("pyautogui")
        module.screenshot = lambda: Shot()
        monkeypatch.setitem(sys.modules, "pyautogui", module)

        from jarvis.nlp.brain import Brain

        def look(_self, system, question, _png, **_kw):
            asked["system"], asked["question"] = system, question
            return reply

        monkeypatch.setattr(Brain, "look", look)
        monkeypatch.setattr(Brain, "available", property(lambda _s: True))
        monkeypatch.setattr(screen.focus, "our_window_rects", list)

    install.asked = asked
    return install


def _ctx(**control) -> Context:
    cfg = Config()
    for key, value in control.items():
        setattr(cfg.control, key, value)
    return Context(config=cfg, speak=lambda _t: None)


def test_a_point_is_scaled_back_to_the_real_screen(looking):
    """The model reads a 1280-wide view of a 2560-wide screen."""
    looking(json.dumps({"found": True, "x": 100, "y": 50, "label": "Local Disk (F:)"}))
    found = screen.find_by_vision(_ctx(), "the third option")
    assert found is not None
    assert found.centre == (200, 100)
    assert found.text == "Local Disk (F:)"
    assert found.source == "vision"


def test_it_is_asked_for_the_thing_the_user_named(looking):
    looking(json.dumps({"found": True, "x": 10, "y": 10, "label": "x"}))
    screen.find_by_vision(_ctx(), "the third option")
    assert "the third option" in looking.asked["question"]


def test_not_found_is_honoured_rather_than_guessed_around(looking):
    looking(json.dumps({"found": False, "why": "no such thing is visible"}))
    assert screen.find_by_vision(_ctx(), "a purple unicorn") is None


def test_a_point_outside_the_image_is_refused(looking):
    looking(json.dumps({"found": True, "x": 9000, "y": 9000, "label": "somewhere"}))
    assert screen.find_by_vision(_ctx(), "anything") is None


def test_it_will_not_point_at_jarvis_itself(looking, monkeypatch):
    """The bar displays the phrase being searched for, so it is a tempting
    thing for a model to find."""
    looking(json.dumps({"found": True, "x": 700, "y": 420, "label": "third option"}))
    monkeypatch.setattr(
        screen.focus, "our_window_rects", lambda: [(1380, 790, 1900, 930)]
    )
    assert screen.find_by_vision(_ctx(), "the third option") is None


@pytest.mark.parametrize(
    "reply",
    ["", "I'm not sure", "{broken", json.dumps([1, 2]), json.dumps({"found": True})],
)
def test_an_unusable_reply_is_a_miss_not_a_crash(looking, reply):
    looking(reply)
    assert screen.find_by_vision(_ctx(), "anything") is None


def test_a_fenced_reply_is_still_read(looking):
    looking('```json\n{"found": true, "x": 10, "y": 20, "label": "ok"}\n```')
    assert screen.find_by_vision(_ctx(), "anything") is not None


def test_it_can_be_turned_off(looking):
    looking(json.dumps({"found": True, "x": 10, "y": 10, "label": "x"}))
    assert screen.find_by_vision(_ctx(visual_fallback=False), "anything") is None


def test_it_is_never_the_first_thing_tried(router, no_real_input, monkeypatch):
    """A screenshot leaving the machine is not the cost of an easy lookup."""
    monkeypatch.setattr(
        screen, "find",
        lambda _l: Target("Sign in", 10, 20, 30, 40, score=100, source="uia"),
    )
    monkeypatch.setattr(
        screen, "find_by_vision",
        lambda *_a: pytest.fail("vision must not run when the tree answered"),
    )
    assert router.dispatch("click Sign in").ok


def test_it_is_tried_before_giving_up(router, no_real_input, monkeypatch):
    monkeypatch.setattr(screen, "find", lambda _l: None)
    monkeypatch.setattr(
        screen, "find_by_vision",
        lambda _ctx, _l: Target("Local Disk (F:)", 99, 99, 101, 101,
                                score=100, source="vision"),
    )
    result = router.dispatch("click the thing over there")
    assert result.ok
    assert "vision" in result.detail
    assert any(call[0] == "click" for call in no_real_input)


def test_an_unknown_kind_reaches_vision(router, no_real_input, monkeypatch):
    """"option" is not a control type; nothing but looking can answer it."""
    seen = {}

    def fake_vision(_ctx, label):
        seen["label"] = label
        return Target("Local Disk (F:)", 99, 99, 101, 101, score=100, source="vision")

    monkeypatch.setattr(screen, "find_nth", lambda _k, _i: None)
    monkeypatch.setattr(screen, "find_by_vision", fake_vision)
    result = router.dispatch("select third option")
    assert result.ok, result.say
    assert result.data["intent"] == "click_nth"
    assert seen["label"] == "the third option"
