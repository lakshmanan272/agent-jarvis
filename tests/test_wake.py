"""Gating the recogniser behind an acoustic wake word.

Matching "jarvis" in the transcript means transcribing everything first: every
word from the speakers, every line of a video playing in the room, all decoded
in full at RTF 0.38 and then discarded for not starting with the right word.
That is also how a film's dialogue kept arriving as commands. openWakeWord
answers "was I addressed" at RTF 0.025 and the recogniser stays asleep until
the answer is yes.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.speech.wake import FRAME_SAMPLES, WakeWord


class FakeModel:
    """Scores whatever it is told to, so the gate can be tested without audio."""

    def __init__(self, scores: list[float]) -> None:
        self.scores = list(scores)
        self.frames = 0
        self.resets = 0

    def predict(self, frame):
        self.frames += 1
        value = self.scores.pop(0) if self.scores else 0.0
        return {"hey_jarvis": value}

    def reset(self):
        self.resets += 1


def detector(scores, threshold=0.5) -> WakeWord:
    w = WakeWord("hey_jarvis", threshold)
    w._model = FakeModel(scores)
    w.available = True
    return w


def audio(frames: int) -> bytes:
    return np.zeros(FRAME_SAMPLES * frames, dtype=np.int16).tobytes()


def test_silence_does_not_wake():
    w = detector([0.0, 0.01, 0.002])
    assert w.heard(audio(3)) is False


def test_a_confident_score_wakes():
    w = detector([0.0, 0.92])
    assert w.heard(audio(2)) is True


def test_a_score_below_the_threshold_does_not():
    w = detector([0.49])
    assert w.heard(audio(1)) is False


def test_the_threshold_is_configurable():
    assert detector([0.6], threshold=0.9).heard(audio(1)) is False
    assert detector([0.95], threshold=0.9).heard(audio(1)) is True


def test_microphone_blocks_are_reframed():
    """The capture stream is sized for the recogniser, not for this."""
    w = detector([0.0] * 10)
    # three 30 ms blocks are not one 80 ms frame; nothing should be scored yet
    small = np.zeros(480, dtype=np.int16).tobytes()
    w.heard(small)
    assert w._model.frames == 0
    # feed enough for exactly one frame
    for _ in range(2):
        w.heard(small)
    assert w._model.frames >= 1


def test_firing_clears_the_history():
    """Otherwise one "hey Jarvis" keeps firing on the frames that follow."""
    w = detector([0.9])
    w.heard(audio(1))
    assert w._model.resets == 1
    assert w._buffer == b""


def test_an_unavailable_detector_never_fires():
    w = WakeWord("hey_jarvis")
    assert w.available is False
    assert w.heard(audio(4)) is False


def test_inference_failure_is_not_fatal():
    class Broken(FakeModel):
        def predict(self, frame):
            raise RuntimeError("onnx exploded")

    w = detector([])
    w._model = Broken([])
    assert w.heard(audio(2)) is False


# --- the gate in the recogniser -------------------------------------------


def test_recogniser_defaults_to_decoding_when_nothing_gates_it():
    from jarvis.config import Config
    from jarvis.speech.stt import Recognizer

    r = Recognizer(Config().speech, on_final=lambda _t: None)
    assert r.should_decode() is True
    assert r.wake is None  # not loaded until start()


def test_engine_only_decodes_while_awake_and_unmuted(no_real_input):
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    engine = Engine(cfg)

    gate = lambda: engine._is_awake and not engine._muted  # noqa: E731
    assert gate() is False, "asleep at rest: audio goes to the wake word only"

    engine.wake(silent=True)
    assert gate() is True, "a command may follow the wake word"

    engine.ctx.variables["muted"] = True
    assert gate() is False, "voice off overrides being awake"


def test_the_wake_callback_opens_the_window(no_real_input):
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    engine = Engine(cfg)
    assert engine._is_awake is False
    engine.on_wake_word()
    assert engine._is_awake is True


@pytest.mark.parametrize("engine_name", ["openwakeword", "none"])
def test_both_wake_engines_are_configurable(engine_name):
    from jarvis.config import Config

    cfg = Config()
    cfg.speech.wake_engine = engine_name
    assert cfg.speech.wake_engine == engine_name
