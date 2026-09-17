"""Writing prose, reading the screen, and dictating.

These three differ from every other skill: the content does not exist until a
model makes it, or until the user speaks it. The dictation tests matter most --
a password said aloud must not end up in the log or on the HUD.
"""
from __future__ import annotations

import pytest


class StubBrain:
    available = True

    def __init__(self, reply: str | None = "x" * 200) -> None:
        self.reply = reply
        self.requests: list[str] = []

    def compose(self, request: str, words: int = 180) -> str | None:
        self.requests.append(request)
        return self.reply


@pytest.fixture
def with_brain(router):
    brain = StubBrain()
    router.brain = brain
    return brain


# --- writing ---------------------------------------------------------------


def test_writing_types_what_the_model_produced(router, no_real_input, with_brain):
    with_brain.reply = "Vijay is a Tamil actor and politician. " * 6
    result = router.dispatch("write about actor Vijay")
    assert result.ok
    typed = [c[1][0] for c in no_real_input if c[0] == "type_text"]
    assert typed and typed[0] == with_brain.reply


def test_the_topic_reaches_the_model_verbatim(router, no_real_input, with_brain):
    router.dispatch("write about Actor Vijay's political career")
    assert with_brain.requests
    assert "Actor Vijay's political career" in with_brain.requests[0]


@pytest.mark.parametrize(
    "phrase,expected_words",
    [
        ("write a short note about python", 80),
        ("write a detailed essay about python", 400),
        ("write about python", 180),
    ],
)
def test_length_is_taken_from_the_request(
    router, no_real_input, monkeypatch, phrase, expected_words
):
    seen = {}
    brain = StubBrain()

    def compose(request, words=180):
        seen["words"] = words
        return "y" * 200

    brain.compose = compose
    router.brain = brain
    router.dispatch(phrase)
    assert seen["words"] == expected_words


def test_nothing_is_typed_when_the_model_declines(router, no_real_input):
    router.brain = StubBrain(reply=None)
    result = router.dispatch("write about actor Vijay")
    assert result.ok is False
    assert no_real_input == []


def test_writing_without_a_model_says_so(router, no_real_input):
    class Off:
        available = False

        def compose(self, *_a, **_k):
            raise AssertionError("must not be called")

    router.brain = Off()
    result = router.dispatch("write about actor Vijay")
    assert result.ok is False
    assert "without a model" in result.say
    assert no_real_input == []


# --- reading the screen ----------------------------------------------------


def test_describing_the_screen_summarises_what_was_read(
    router, no_real_input, monkeypatch, with_brain
):
    from jarvis.core import screen

    monkeypatch.setattr(
        screen, "_ocr_words",
        lambda: [("Untitled - Notepad", (0, 0, 200, 20)),
                 ("Meeting notes for Friday", (0, 30, 300, 50))],
    )
    with_brain.reply = "You are looking at Notepad with some meeting notes open."
    result = router.dispatch("what's on screen")
    assert result.ok
    assert "Notepad" in result.say
    # the OCR text is passed to the model, not read back at the user
    assert "Meeting notes for Friday" in with_brain.requests[0]


def test_describing_falls_back_to_raw_lines_without_a_model(
    router, no_real_input, monkeypatch
):
    from jarvis.core import screen

    class Off:
        available = False

    router.brain = Off()
    monkeypatch.setattr(
        screen, "_ocr_words", lambda: [("Untitled - Notepad", (0, 0, 200, 20))]
    )
    result = router.dispatch("what do you see")
    assert result.ok
    assert "Notepad" in result.say


def test_describing_an_unreadable_screen_fails_cleanly(
    router, no_real_input, monkeypatch
):
    from jarvis.core import screen

    monkeypatch.setattr(screen, "_ocr_words", list)
    result = router.dispatch("what's on screen")
    assert result.ok is False


# --- dictation -------------------------------------------------------------


def _engine():
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    engine = Engine(cfg)
    engine.wake(silent=True)
    return engine


def test_dictation_types_speech_instead_of_running_it(no_real_input):
    engine = _engine()
    engine.submit("start dictation", "text")
    no_real_input.clear()
    engine.on_final("open chrome")           # a command, but we are dictating
    typed = [c[1][0] for c in no_real_input if c[0] == "type_text"]
    assert typed == ["open chrome "]         # typed, not executed


def test_stopping_is_still_obeyed_while_dictating(no_real_input):
    engine = _engine()
    engine.submit("start dictation", "text")
    engine.on_final("stop dictation")
    assert engine.ctx.variables["dictating"] is False


def test_commands_work_again_after_dictation(no_real_input):
    engine = _engine()
    engine.submit("start dictation", "text")
    engine.submit("stop dictation", "text")
    no_real_input.clear()
    engine.on_final("select all")
    assert any(c[0] == "hotkey" for c in no_real_input)


def test_private_dictation_is_not_shown(no_real_input):
    """A spoken password must not appear on a HUD anyone can see."""
    from jarvis.bus import BUS, HEARD

    engine = _engine()
    engine.submit("private dictation", "text")
    seen = []
    BUS.on(HEARD, seen.append)
    try:
        engine.on_final("hunter2 is my password")
    finally:
        BUS.off(HEARD, seen.append)
    assert seen, "the bar should still show that something was heard"
    shown = seen[-1]["text"]
    assert "hunter2" not in shown
    assert set(shown) == {"\u2022"}


def test_private_dictation_is_not_logged(no_real_input, caplog):
    import logging

    engine = _engine()
    engine.submit("private dictation", "text")
    with caplog.at_level(logging.DEBUG):
        engine.on_final("hunter2 is my password")
    assert "hunter2" not in caplog.text


def test_private_dictation_still_types_the_real_text(no_real_input):
    engine = _engine()
    engine.submit("private dictation", "text")
    no_real_input.clear()
    engine.on_final("hunter2")
    typed = [c[1][0] for c in no_real_input if c[0] == "type_text"]
    assert typed == ["hunter2 "]


def test_ordinary_dictation_is_shown(no_real_input):
    from jarvis.bus import BUS, HEARD

    engine = _engine()
    engine.submit("start dictation", "text")
    seen = []
    BUS.on(HEARD, seen.append)
    try:
        engine.on_final("dear sir")
    finally:
        BUS.off(HEARD, seen.append)
    assert seen[-1]["text"] == "dear sir"
