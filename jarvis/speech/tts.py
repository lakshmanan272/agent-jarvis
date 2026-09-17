"""Spoken replies via SAPI5 (offline, no network round trip).

pyttsx3's runAndWait() blocks, so speech runs on its own thread with a queue.
A command's *action* must never wait on its acknowledgement being read out.
"""
from __future__ import annotations

import logging
import queue
import threading

log = logging.getLogger("jarvis.tts")


class Speaker:
    def __init__(self, cfg, on_start=None, on_end=None) -> None:
        self.cfg = cfg
        self.on_start = on_start or (lambda: None)
        self.on_end = on_end or (lambda: None)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._engine = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tts", daemon=True)
        self._thread.start()
        # Engine init takes ~200 ms; do it now so the first reply isn't slow.
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        self._queue.put(None)
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text or not self.cfg.enabled:
            return
        if len(text) > self.cfg.max_chars:
            text = text[: self.cfg.max_chars].rsplit(" ", 1)[0] + "."
        self._queue.put(text)

    def shush(self) -> None:
        """Drop anything queued but not yet spoken."""
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                if item is None:  # keep the shutdown sentinel
                    self._queue.put(None)
                    return
            except queue.Empty:
                return

    def _run(self) -> None:
        try:
            import pyttsx3

            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", self.cfg.rate)
            self._engine.setProperty("volume", self.cfg.volume)
            if self.cfg.voice_hint:
                for voice in self._engine.getProperty("voices"):
                    if self.cfg.voice_hint.lower() in voice.name.lower():
                        self._engine.setProperty("voice", voice.id)
                        break
        except Exception as exc:
            log.warning("text-to-speech unavailable: %s", exc)
            self._ready.set()
            return
        self._ready.set()

        while True:
            text = self._queue.get()
            if text is None:
                break
            try:
                self.on_start()
                self._engine.say(text)
                self._engine.runAndWait()
            except Exception:
                log.debug("speech failed for %r", text, exc_info=True)
            finally:
                self.on_end()
