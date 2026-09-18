"""The loop that decides for itself.

Everything else in Jarvis is told what to do. This one is handed a goal and
works out the actions, which makes its guard rails the interesting part: it
must ask first, it must stop when told, it must never click its own window,
and it must refuse a coordinate rather than clamp it into something plausible.
"""
from __future__ import annotations

import json

import pytest

from jarvis.skills import autopilot


@pytest.fixture
def screen(monkeypatch):
    """A fixed 1280x720 view of a 2560x1440 screen: scale factor 2."""
    monkeypatch.setattr(
        autopilot, "_screenshot", lambda: (b"png-bytes", 1280, 720, 2.0)
    )
    monkeypatch.setattr(autopilot, "SETTLE_S", 0)
    monkeypatch.setattr(autopilot.focus, "our_window_rects", list)


@pytest.fixture
def decides(monkeypatch):
    """Queue up the model's replies, and record what it was asked."""
    asked: list[str] = []

    def install(*replies):
        queue = list(replies)

        def look(_self, system, question, _png, **_kw):
            asked.append(f"{system}\n{question}")
            return queue.pop(0) if queue else json.dumps(
                {"action": "done", "message": "finished"}
            )

        from jarvis.nlp.brain import Brain

        monkeypatch.setattr(Brain, "look", look)
        monkeypatch.setattr(Brain, "available", property(lambda _s: True))

    install.asked = asked
    return install


def _run(goal: str = "do the thing"):
    from jarvis.config import Config
    from jarvis.core.router import Router
    from jarvis.skills.base import Context

    cfg = Config()
    cfg.control.confirm_destructive = False  # the confirmation has its own test
    ctx = Context(config=cfg, speak=lambda _t: None)
    return Router(ctx).dispatch(f"work out {goal}")


def _say(**step) -> str:
    return json.dumps(step)


# --- it asks before it drives ----------------------------------------------


def test_it_confirms_before_taking_over(no_real_input):
    from jarvis.config import Config
    from jarvis.core.router import Router
    from jarvis.skills.base import Context

    cfg = Config()
    cfg.control.confirm_destructive = True
    ctx = Context(config=cfg, speak=lambda _t: None)
    result = Router(ctx).dispatch("work out how to turn on dark mode")
    assert "yes or no" in result.say
    assert "screen" in result.say, "the user should know a screenshot is sent"
    assert no_real_input == [], "nothing may happen before the answer"


# --- coordinates -----------------------------------------------------------


def test_coordinates_are_scaled_back_to_the_real_screen(
    screen, decides, no_real_input
):
    decides(_say(action="click", x=100, y=50, thought="the button"))
    _run()
    clicks = [c for c in no_real_input if c[0] == "click"]
    assert clicks, "no click was performed"
    assert clicks[0][2]["x"] == 200 and clicks[0][2]["y"] == 100


def test_a_coordinate_off_the_screen_is_refused_not_clamped(
    screen, decides, no_real_input
):
    decides(
        _say(action="click", x=9000, y=9000, thought="somewhere"),
        _say(action="done", message="stopped"),
    )
    result = _run()
    assert [c for c in no_real_input if c[0] == "click"] == []
    assert "refused" in result.detail


def test_it_never_clicks_its_own_window(screen, decides, no_real_input, monkeypatch):
    """The model is told where Jarvis is; this is what happens when it forgets."""
    monkeypatch.setattr(
        autopilot.focus, "our_window_rects", lambda: [(0, 0, 400, 400)]
    )
    decides(
        _say(action="click", x=50, y=50, thought="that looks clickable"),
        _say(action="done", message="stopped"),
    )
    result = _run()
    assert [c for c in no_real_input if c[0] == "click"] == []
    assert "Jarvis" in result.detail


def test_the_forbidden_zone_is_given_in_the_image_scale(monkeypatch):
    monkeypatch.setattr(
        autopilot.focus, "our_window_rects", lambda: [(1000, 200, 1200, 600)]
    )
    note = autopilot._forbidden_note(2.0)
    assert "x 500-600" in note and "y 100-300" in note


# --- stopping --------------------------------------------------------------


def test_done_ends_the_loop_immediately(screen, decides, no_real_input):
    decides(_say(action="done", message="dark mode is on"))
    result = _run()
    assert result.ok and result.say == "dark mode is on"
    assert result.data["steps"] == 1


def test_give_up_is_a_failure_not_a_silent_success(screen, decides):
    decides(_say(action="give_up", message="the setting is not on this screen"))
    result = _run()
    assert result.ok is False
    assert "not on this screen" in result.say


def test_it_runs_out_of_steps_rather_than_looping_forever(
    screen, decides, no_real_input
):
    decides(*[_say(action="scroll", amount=-3, thought="looking") for _ in range(50)])
    result = _run()
    assert result.ok is False
    assert str(autopilot.MAX_STEPS) in result.say
    scrolls = [c for c in no_real_input if c[0] == "scroll"]
    assert len(scrolls) == autopilot.MAX_STEPS


def test_pressing_end_abandons_the_loop(screen, decides, no_real_input):
    from jarvis.core import actuator

    decides(*[_say(action="scroll", amount=-3) for _ in range(50)])
    actuator.abort()
    try:
        with pytest.raises(InterruptedError):
            autopilot.do_autopilot(
                _ctx(), goal="anything", _confirmed=True
            )
    finally:
        actuator.clear_abort()
    assert no_real_input == []


def _ctx():
    from jarvis.config import Config
    from jarvis.skills.base import Context

    cfg = Config()
    cfg.control.confirm_destructive = False
    return Context(config=cfg, speak=lambda _t: None)


# --- reading the model's answer --------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        '{"action": "done", "message": "x"}',
        'Sure!\n```json\n{"action": "done", "message": "x"}\n```',
        'Here is the step: {"action": "done", "message": "x"} -- hope that helps',
    ],
)
def test_a_decision_is_found_however_it_is_wrapped(raw):
    assert autopilot._decision(raw)["action"] == "done"


@pytest.mark.parametrize("raw", ["", "no json here", "{not valid}", "[1, 2]"])
def test_an_unreadable_reply_is_not_guessed_at(raw):
    assert autopilot._decision(raw) is None


def test_an_unreadable_reply_stops_rather_than_acting(screen, decides, no_real_input):
    decides("I'm not sure what you want me to do.")
    result = _run()
    assert result.ok is False
    assert no_real_input == []
