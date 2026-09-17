"""Jarvis listens through the speakers too.

With a video playing, its dialogue reaches the microphone and — inside the
follow-up window, where no wake word is required — was dispatched as commands.
The log filled with things like "the in their eyes much learn a new and up at",
each one answered with a spoken "I don't know how to ...".
"""
from __future__ import annotations

import pytest

LYRICS = [
    "the in their eyes much learn a new and up at",
    "i'm in early on",
    "occurring",
    "did you mean theme song",
    "and then she said it was over",
]

COMMANDS = [
    "open chrome",
    "play theme song",
    "click",
    "scroll down",
    "type hello there",
    "what time is it",
]


@pytest.mark.parametrize("phrase", LYRICS)
def test_stray_speech_is_not_a_command(router, phrase):
    assert router.can_handle(phrase) is False


@pytest.mark.parametrize("phrase", COMMANDS)
def test_real_commands_still_pass(router, phrase):
    assert router.can_handle(phrase) is True


def test_can_handle_has_no_side_effects(router, no_real_input, side_effects):
    router.can_handle("open chrome and play a song and shutdown the computer")
    assert no_real_input == []
    assert side_effects == []
    assert router.ctx.pending_confirm is None


def _engine():
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    return Engine(cfg)


def test_unaddressed_noise_is_ignored_during_the_follow_up_window(no_real_input):
    engine = _engine()
    engine.wake(silent=True)  # open the follow-up window
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("the in their eyes much learn a new and up at")
    assert dispatched == []


def test_unaddressed_commands_still_work_during_the_window(no_real_input):
    engine = _engine()
    engine.wake(silent=True)
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("scroll down")
    assert dispatched == ["scroll down"]


def test_being_addressed_by_name_always_gets_an_answer(no_real_input):
    """Say "Jarvis" and you deserve a reply, even if it is "I can't"."""
    engine = _engine()
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("jarvis do a backflip")
    assert dispatched == ["do a backflip"]


def test_bare_wake_word_only_wakes(no_real_input):
    engine = _engine()
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("jarvis")
    assert dispatched == []


def test_nothing_is_dispatched_when_asleep(no_real_input):
    engine = _engine()
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("scroll down")  # never woken
    assert dispatched == []
