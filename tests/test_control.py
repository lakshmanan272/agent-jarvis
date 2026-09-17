"""Turning the microphone off, and stopping a command that is already running."""
from __future__ import annotations


class FakeRecognizer:
    """Records pause/resume so "off" can be shown to mean off."""

    def __init__(self) -> None:
        self.paused = False
        self.history: list[str] = []

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def pause(self) -> None:
        self.paused = True
        self.history.append("pause")

    def resume(self) -> None:
        self.paused = False
        self.history.append("resume")


def _engine():
    from jarvis.config import Config
    from jarvis.core.engine import Engine

    cfg = Config()
    cfg.speech.enabled = False
    cfg.voice_out.enabled = False
    engine = Engine(cfg)
    engine.recognizer = FakeRecognizer()
    return engine


# --- voice off means off ---------------------------------------------------


def test_muting_stops_the_recogniser(no_real_input):
    """Not merely ignoring what it hears -- it stops listening."""
    engine = _engine()
    engine.set_muted(True)
    assert engine.recognizer.paused is True
    assert engine.is_muted is True


def test_unmuting_starts_it_again(no_real_input):
    engine = _engine()
    engine.set_muted(True)
    engine.set_muted(False)
    assert engine.recognizer.paused is False
    assert engine.is_muted is False


def test_nothing_is_transcribed_to_the_screen_while_muted(no_real_input):
    """The bar kept showing heard text with voice off, which looked broken."""
    from jarvis.bus import BUS, HEARD

    engine = _engine()
    engine.set_muted(True)
    seen = []
    BUS.on(HEARD, seen.append)
    try:
        engine.on_final("open chrome")
        engine.on_partial("open chr")
    finally:
        BUS.off(HEARD, seen.append)
    assert seen == []


def test_muting_closes_the_follow_up_window(no_real_input):
    engine = _engine()
    engine.wake(silent=True)
    engine.set_muted(True)
    dispatched = []
    engine.submit = lambda text, source="text": dispatched.append(text)
    engine.on_final("scroll down")
    assert dispatched == []


def test_finishing_a_spoken_reply_does_not_unmute(no_real_input):
    """The pause taken so Jarvis cannot hear itself must not override the user."""
    engine = _engine()
    engine.set_muted(True)
    engine.recognizer.history.clear()
    engine._on_speech_end()
    assert engine.recognizer.history == []
    assert engine.is_muted is True


def test_typing_still_works_with_the_microphone_off(no_real_input):
    engine = _engine()
    engine.set_muted(True)
    result = engine.submit("select all", "text")
    assert result is not None and result.ok


# --- end stops what is running ---------------------------------------------


def test_end_raises_the_abort_flag(no_real_input):
    """It used to clear the flag microseconds after setting it, so in-flight
    work on another thread never saw it."""
    from jarvis.core import actuator

    engine = _engine()
    actuator.clear_abort()
    engine.panic()
    assert actuator.aborted() is True
    actuator.clear_abort()


def test_a_new_command_runs_after_end(no_real_input):
    from jarvis.core import actuator

    engine = _engine()
    engine.panic()
    result = engine.submit("select all", "text")
    assert result is not None and result.ok
    assert actuator.aborted() is False, "submit must clear the abort first"


def test_end_drops_a_pending_confirmation(no_real_input, side_effects):
    engine = _engine()
    engine.submit("shutdown the computer", "text")
    assert engine.ctx.pending_confirm is not None
    engine.panic()
    assert engine.ctx.pending_confirm is None
    # a later "yes" must not resurrect it
    engine.submit("yes", "text")
    assert side_effects == []
    from jarvis.core import actuator

    actuator.clear_abort()


def test_an_abort_mid_chain_abandons_the_rest(router, no_real_input, monkeypatch):
    """Stopping means stopping, not failing one step and pressing on."""
    from jarvis.core import actuator

    calls = []

    def abort_on_second(*_a, **_k):
        calls.append("hotkey")
        if len(calls) == 2:
            raise InterruptedError("end pressed")

    monkeypatch.setattr(actuator, "hotkey", abort_on_second)
    result = router.dispatch("select all and copy and paste and undo")
    assert result.say == "Stopped."
    assert result.data["completed"] == ["select all"]
    assert len(calls) == 2, "later steps must not have run"


def test_end_reports_itself(no_real_input):
    from jarvis.bus import BUS, RESULT
    from jarvis.core import actuator

    engine = _engine()
    seen = []
    BUS.on(RESULT, seen.append)
    try:
        engine.panic()
    finally:
        BUS.off(RESULT, seen.append)
    assert seen and seen[0].say == "Stopped."
    actuator.clear_abort()
