"""The orchestrator: microphone and hotkeys in, actions out.

Latency budget for a spoken command, measured on a mid-range laptop:
    audio block            30 ms
    Vosk incremental decode  ~40 ms (already done when speech stops)
    endpoint detection     ~250 ms of trailing silence
    normalise + route      <1 ms
    actuate                5-40 ms
Total ~300-400 ms from the last syllable to the click. The endpoint wait is the
dominant term, which is why `partial_dispatch` exists: an exact-match partial
("click", "scroll down") fires without waiting for silence at all.
"""
from __future__ import annotations

import logging
import threading
import time

from jarvis.bus import BUS, COMMAND, ERROR, FOCUS_CONSOLE, HEARD, RESULT, SAY, STATE
from jarvis.core import actuator as act
from jarvis.core.router import Router
from jarvis.nlp.brain import Brain
from jarvis.nlp.matcher import normalize, strip_wake
from jarvis.skills.base import ActionResult, Context

log = logging.getLogger("jarvis.engine")

# Short, unambiguous commands worth firing from a partial hypothesis. Anything
# with a free-text tail ("type ...", "search ...") must wait for the final
# result, or we would act on half a sentence.
INSTANT_COMMANDS = frozenset(
    {
        "click", "double click", "right click", "middle click",
        "scroll up", "scroll down", "scroll left", "scroll right",
        "copy", "paste", "cut", "undo", "redo", "save", "stop",
        "back", "forward", "refresh", "new tab", "close tab",
        "next tab", "previous tab", "select all", "minimize", "maximize",
        "mute", "pause", "play", "next track", "screenshot", "tab", "space",
        "new line", "delete", "yes", "no",
    }
)


class Engine:
    def __init__(self, config) -> None:
        self.config = config
        act.configure(config.control)

        from jarvis.speech.tts import Speaker

        self.speaker = Speaker(
            config.voice_out,
            on_start=self._on_speech_start,
            on_end=self._on_speech_end,
        )
        self.ctx = Context(config=config, speak=self.speaker.say)
        self.brain = Brain(config.brain)
        self.router = Router(self.ctx, brain=self.brain)
        self.ctx.variables["router"] = self.router

        self.recognizer = None
        self._awake_until = 0.0
        self._last_dispatch = ""
        self._last_dispatch_at = 0.0
        self._lock = threading.Lock()          # guards the duplicate check
        self._dispatch_lock = threading.Lock()  # serialises actual execution
        self._running = threading.Event()
        self._state = "idle"

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self._running.set()
        self.speaker.start()
        self._start_speech()
        self._bind_hotkeys()
        self._set_state("idle")
        log.info("jarvis online")

    def stop(self) -> None:
        self._running.clear()
        if self.recognizer is not None:
            self.recognizer.stop()
        self.speaker.stop()
        try:
            import keyboard

            keyboard.unhook_all()
        except Exception:
            pass
        log.info("jarvis offline")

    @property
    def running(self) -> bool:
        return self._running.is_set() and not self.ctx.variables.get("exit")

    def _start_speech(self) -> None:
        if not self.config.speech.enabled:
            from jarvis.speech.stt import NullRecognizer

            self.recognizer = NullRecognizer()
            self.recognizer.start()
            return
        from jarvis.speech.stt import ModelMissing, NullRecognizer, Recognizer

        try:
            self.recognizer = Recognizer(
                self.config.speech, on_final=self.on_final, on_partial=self.on_partial
            )
            self.recognizer.start()
        except ModelMissing as exc:
            BUS.emit(ERROR, str(exc))
            log.error("%s", exc)
            self.recognizer = NullRecognizer()
            self.recognizer.start()
        except Exception as exc:
            BUS.emit(ERROR, f"Microphone unavailable: {exc}")
            log.exception("speech startup failed")
            self.recognizer = NullRecognizer()
            self.recognizer.start()

    def _bind_hotkeys(self) -> None:
        try:
            import keyboard
        except ImportError:
            log.warning("global hotkeys unavailable (keyboard module missing)")
            return
        hk = self.config.hotkeys
        bindings = {
            hk.push_to_talk: self.wake,
            hk.panic_stop: self.panic,
            hk.toggle_mute: self.toggle_mute,
            hk.text_console: self.show_console,
        }
        for combo, fn in bindings.items():
            try:
                keyboard.add_hotkey(combo, fn, suppress=False, trigger_on_release=False)
            except Exception as exc:
                # Usually means the combo is taken by another app; not fatal.
                log.warning("could not bind %s: %s", combo, exc)

    # --- speech in ---------------------------------------------------------

    def on_partial(self, text: str) -> None:
        BUS.emit(HEARD, {"text": text, "final": False})
        if not self.config.speech.partial_dispatch or self._muted:
            return
        heard, phrase = strip_wake(text, self.config.speech.wake_words)
        if heard:
            self.wake(silent=True)
        if not self._is_awake:
            return
        candidate = normalize(phrase if heard else text)
        if candidate in INSTANT_COMMANDS:
            self.submit(candidate, source="voice-partial")

    def on_final(self, text: str) -> None:
        BUS.emit(HEARD, {"text": text, "final": True})
        if self._muted:
            return
        heard, phrase = strip_wake(text, self.config.speech.wake_words)
        if heard:
            self.wake(silent=True)
            if not phrase.strip():
                return  # bare "Jarvis" is just a wake, not a command
        elif not (self._is_awake or self.config.speech.always_on):
            return
        self.submit(phrase if heard else text, source="voice")

    # --- command in --------------------------------------------------------

    def submit(self, text: str, source: str = "text") -> ActionResult | None:
        """Route and execute one command. Safe to call from any thread."""
        text = (text or "").strip()
        if not text:
            return None
        with self._lock:
            if self._is_duplicate(text):
                return None
            self._last_dispatch = text
            self._last_dispatch_at = time.monotonic()

        BUS.emit(COMMAND, {"text": text, "source": source})
        self._set_state("acting")
        act.clear_abort()
        # Serialised deliberately: speech arrives on the decoder thread while
        # typed commands arrive on the UI thread, and two handlers driving the
        # keyboard at once would interleave their keystrokes into one another.
        with self._dispatch_lock:
            try:
                result = self.router.dispatch(text)
            except InterruptedError:
                result = ActionResult(ok=True, say="Stopped.")
            except Exception as exc:
                log.exception("dispatch blew up")
                result = ActionResult.fail("Something went wrong.", str(exc))

        # Only remember commands that worked, so "repeat" can't replay a typo.
        if result.ok and result.data.get("intent") != "repeat":
            self.ctx.variables["previous_command"] = text

        BUS.emit(RESULT, result)
        if result.say:
            BUS.emit(SAY, result.say)
            self.speaker.say(result.say)
        elif not result.ok:
            self.speaker.say(result.say or "Didn't get that.")

        self._extend_conversation()
        self._set_state("listening" if self._is_awake else "idle")
        return result

    def _is_duplicate(self, text: str) -> bool:
        """Suppress the partial/final double-fire of the same phrase.

        A phrase dispatched from a partial arrives again ~250 ms later as the
        final result. Without this, every "click" would click twice.
        """
        if text != self._last_dispatch:
            return False
        return (time.monotonic() - self._last_dispatch_at) < 1.2

    # --- control -----------------------------------------------------------

    @property
    def _is_awake(self) -> bool:
        return (
            self.config.speech.always_on
            or time.monotonic() < self._awake_until
        )

    @property
    def _muted(self) -> bool:
        return bool(self.ctx.variables.get("muted"))

    def _extend_conversation(self) -> None:
        self._awake_until = time.monotonic() + self.config.speech.conversation_timeout_s

    def wake(self, silent: bool = False) -> None:
        was_awake = self._is_awake
        self.ctx.variables["muted"] = False
        self._extend_conversation()
        self._set_state("listening")
        if not was_awake and not silent:
            BUS.emit(SAY, "Yes?")
            self.speaker.say("Yes?")

    def panic(self) -> None:
        act.abort()
        self.speaker.shush()
        self.ctx.pending_confirm = None
        self._awake_until = 0.0
        self._set_state("idle")
        BUS.emit(RESULT, ActionResult(ok=True, say="Stopped."))
        act.clear_abort()

    def toggle_mute(self) -> None:
        muted = not self._muted
        self.ctx.variables["muted"] = muted
        self._set_state("muted" if muted else "idle")

    def show_console(self) -> None:
        """Ask the UI to reveal the text bar. A no-op headless (nothing listens)."""
        BUS.emit(FOCUS_CONSOLE)

    # --- state -------------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state == self._state:
            return
        self._state = state
        BUS.emit(STATE, state)

    def _on_speech_start(self) -> None:
        # Deafen the mic while the speakers are active: without this Jarvis
        # transcribes its own replies and executes them.
        if self.recognizer is not None:
            self.recognizer.pause()
        self._set_state("speaking")

    def _on_speech_end(self) -> None:
        if self.recognizer is not None:
            self.recognizer.resume()
        self._set_state("listening" if self._is_awake else "idle")
