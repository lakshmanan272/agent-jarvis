"""Streaming offline speech recognition (Vosk).

Why Vosk rather than Whisper: Whisper is more accurate but batch-oriented — you
wait for an utterance to end, then decode, and a short command costs 400-900 ms.
Vosk decodes incrementally as audio arrives, so by the time the user stops
speaking the transcript is already there. For a "do it in under a second" agent
that difference is the whole product.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

import numpy as np

from jarvis.config import MODEL_DIR, SpeechConfig

log = logging.getLogger("jarvis.stt")

# A block counts as speech when it stands this far above the ambient floor,
# with an absolute floor so a dead-silent room cannot make the ratio trigger
# on its own noise.
SPEECH_OVER_NOISE = 3.0
MIN_SPEECH_RMS = 130.0


class ModelMissing(RuntimeError):
    pass


def _is_model(path: Path) -> bool:
    return path.is_dir() and (path / "am").exists()


def ensure_model(url: str, target_dir: Path = MODEL_DIR) -> Path:
    """Return the local directory for *this* model, downloading it once if needed.

    Matched by the name the archive unpacks to rather than "any model already
    here": several can coexist, and switching accuracy has to actually switch
    rather than silently keep whichever was downloaded first.
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_name = url.rsplit("/", 1)[-1]
    expected = target_dir / archive_name.removesuffix(".zip")
    if _is_model(expected):
        return expected

    import urllib.request

    archive = target_dir / archive_name
    log.info("downloading speech model from %s (one time)", url)
    try:
        urllib.request.urlretrieve(url, archive)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(target_dir)
    except Exception as exc:  # network down, proxy, disk full...
        raise ModelMissing(
            f"Could not fetch the speech model: {exc}. "
            f"Download it manually from {url} and unzip it into {target_dir}."
        ) from exc
    finally:
        archive.unlink(missing_ok=True)

    if _is_model(expected):
        return expected
    # The archive did not use the name we predicted; take whatever it did unpack.
    found = [p for p in target_dir.iterdir() if _is_model(p)]
    if not found:
        raise ModelMissing(f"Model archive did not contain a model in {target_dir}")
    return found[0]


class Recognizer:
    """Microphone -> transcripts, on a background thread.

    Emits two kinds of callback:
      on_partial(text)  - interim hypothesis, updated several times a second
      on_final(text)    - the utterance as decoded once speech stops
    """

    def __init__(
        self,
        cfg: SpeechConfig,
        on_final: Callable[[str], None],
        on_partial: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.on_final = on_final
        self.on_partial = on_partial or (lambda _t: None)
        self._audio: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._recognizer = None

    # --- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        import sounddevice as sd
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)  # Kaldi is extremely chatty on stderr otherwise
        model_path = ensure_model(self.cfg.resolve_model_url())
        log.info("loading acoustic model from %s", model_path)
        model = Model(str(model_path))
        self._recognizer = KaldiRecognizer(model, self.cfg.sample_rate)
        self._recognizer.SetWords(False)

        block = int(self.cfg.sample_rate * self.cfg.block_ms / 1000)
        self._stream = sd.RawInputStream(
            samplerate=self.cfg.sample_rate,
            blocksize=block,
            device=self.cfg.device,
            dtype="int16",
            channels=1,
            callback=self._audio_callback,
        )
        self._stream.start()
        self._thread = threading.Thread(target=self._decode_loop, name="stt", daemon=True)
        self._thread.start()
        log.info(
            "microphone open at %d Hz, %d ms blocks",
            self.cfg.sample_rate,
            self.cfg.block_ms,
        )

    def stop(self) -> None:
        self._stop.set()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                log.debug("stream close failed", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def pause(self) -> None:
        """Stop emitting transcripts — used while Jarvis is speaking."""
        self._paused.set()

    def resume(self) -> None:
        # Drop whatever was captured while paused so Jarvis never hears itself.
        while not self._audio.empty():
            try:
                self._audio.get_nowait()
            except queue.Empty:
                break
        if self._recognizer is not None:
            self._recognizer.Reset()
        self._paused.clear()

    # --- internals ---------------------------------------------------------

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        if status:
            log.debug("audio status: %s", status)
        if self._paused.is_set():
            return
        try:
            self._audio.put_nowait(bytes(indata))
        except queue.Full:
            # Better to drop a 30 ms block than to build unbounded latency.
            log.debug("audio queue full, dropping a block")

    def _level(self, chunk: bytes) -> float:
        """RMS amplitude of one block, 0..32768."""
        samples = np.frombuffer(chunk, dtype=np.int16)
        if samples.size == 0:
            return 0.0
        # float64 accumulate: int16 squares overflow int16 immediately.
        return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))

    def _decode_loop(self) -> None:
        """Feed audio to Vosk, and decide for ourselves when the user stopped.

        Vosk has its own endpointer, but it is tuned for dictation and waits out
        a long pause before finalising. For a command agent that pause *is* the
        latency: the words are already decoded, we are only waiting to be told
        the sentence ended. So we watch the signal level ourselves and force a
        final result after a short run of quiet blocks, typically cutting a few
        hundred milliseconds off every spoken command.
        """
        last_partial = ""
        silent_blocks = 0
        heard_speech = False
        # Adaptive, because "quiet" in a cafe is not "quiet" in a bedroom. Seeded
        # high so a noisy first second cannot wedge the gate shut.
        noise_floor = 200.0
        quiet_blocks_needed = max(
            1, int(self.cfg.endpoint_silence_ms / self.cfg.block_ms)
        )

        while not self._stop.is_set():
            try:
                chunk = self._audio.get(timeout=0.2)
            except queue.Empty:
                continue

            level = self._level(chunk)
            speaking = level > max(noise_floor * SPEECH_OVER_NOISE, MIN_SPEECH_RMS)
            if speaking:
                heard_speech = True
                silent_blocks = 0
            else:
                silent_blocks += 1
                # Track the floor only while quiet, and only drift downwards
                # quickly / upwards slowly, so one cough cannot deafen us.
                weight = 0.05 if level < noise_floor else 0.005
                noise_floor = (1 - weight) * noise_floor + weight * level

            if self._recognizer.AcceptWaveform(chunk):
                # Vosk got there first (a long utterance, or a hard stop).
                text = json.loads(self._recognizer.Result()).get("text", "").strip()
                last_partial, silent_blocks, heard_speech = "", 0, False
                if text:
                    self.on_final(text)
                continue

            partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()

            if heard_speech and silent_blocks >= quiet_blocks_needed and partial:
                # The speaker has stopped and we already have the words. Close
                # the utterance now instead of waiting for Vosk to agree.
                text = json.loads(self._recognizer.FinalResult()).get("text", "").strip()
                last_partial, silent_blocks, heard_speech = "", 0, False
                if text:
                    self.on_final(text)
                continue

            if not partial or partial == last_partial:
                continue
            last_partial = partial
            self.on_partial(partial)


class NullRecognizer:
    """Stand-in when speech is disabled, so the engine needs no branching."""

    def start(self) -> None:
        log.info("speech disabled; text console only")

    def stop(self) -> None:
        pass

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass
