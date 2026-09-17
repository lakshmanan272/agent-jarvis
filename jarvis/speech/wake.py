"""Hearing one phrase, cheaply, so the recogniser can stay asleep.

Matching the wake word in the *transcript* — which is what this did before —
means transcribing everything first. Every word from the speakers, the video
playing in the background, the conversation in the room: all of it decoded in
full, at real-time factor 0.38 for the accurate model, only to be thrown away
because it did not begin with "Jarvis". That is also why a film's dialogue kept
arriving as commands.

openWakeWord answers the one question that matters — *was I addressed* — for
RTF 0.025, about fifteen times cheaper, and it matches the acoustic shape of
the phrase rather than a spelling the recogniser has to get right first. The
full recogniser only runs once the answer is yes.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.wake")

# openWakeWord is trained on 80 ms frames of 16 kHz audio.
FRAME_SAMPLES = 1280


class WakeWord:
    """Streaming wake-word detector. Feed it audio; it says when it was called."""

    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        self.model_name = model
        self.threshold = threshold
        self._model = None
        self._buffer = b""
        self.available = False

    def start(self) -> bool:
        """Load the model. False if openWakeWord is not installed or has no models."""
        try:
            import openwakeword
            from openwakeword.model import Model
        except ImportError:
            log.info("openwakeword not installed; falling back to transcript matching")
            return False

        try:
            # No-op once the files are present; the first run fetches ~6 MB.
            openwakeword.utils.download_models()
            self._model = Model(
                wakeword_models=[self.model_name], inference_framework="onnx"
            )
        except Exception as exc:
            log.warning("could not load wake word %r: %s", self.model_name, exc)
            return False

        self.available = True
        log.info("wake word %r ready (threshold %.2f)", self.model_name, self.threshold)
        return True

    def heard(self, chunk: bytes) -> bool:
        """True when the wake word completes inside this audio.

        Accepts whatever block size the microphone produces and re-frames it,
        because openWakeWord wants exactly 1280 samples and the capture stream
        is configured for the recogniser's convenience, not this one's.
        """
        if not self.available:
            return False
        import numpy as np

        self._buffer += chunk
        needed = FRAME_SAMPLES * 2  # int16
        fired = False
        while len(self._buffer) >= needed:
            frame = np.frombuffer(self._buffer[:needed], dtype=np.int16)
            self._buffer = self._buffer[needed:]
            try:
                scores = self._model.predict(frame)
            except Exception:
                log.debug("wake inference failed", exc_info=True)
                continue
            if any(score >= self.threshold for score in scores.values()):
                fired = True
        if fired:
            self.reset()
        return fired

    def reset(self) -> None:
        """Forget the recent past, so one phrase cannot fire twice."""
        self._buffer = b""
        if self._model is not None:
            try:
                self._model.reset()
            except Exception:
                log.debug("wake reset failed", exc_info=True)
