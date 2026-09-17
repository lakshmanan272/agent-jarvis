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

from jarvis.config import MODEL_DIR, SpeechConfig

log = logging.getLogger("jarvis.stt")


class ModelMissing(RuntimeError):
    pass


def ensure_model(url: str, target_dir: Path = MODEL_DIR) -> Path:
    """Return a local Vosk model directory, downloading it once if needed."""
    target_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        p for p in target_dir.iterdir() if p.is_dir() and (p / "am").exists()
    ]
    if existing:
        return existing[0]

    import urllib.request

    name = url.rsplit("/", 1)[-1]
    archive = target_dir / name
    log.info("downloading speech model (~40 MB) from %s", url)
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

    found = [p for p in target_dir.iterdir() if p.is_dir() and (p / "am").exists()]
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
        model_path = ensure_model(self.cfg.model_url)
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

    def _decode_loop(self) -> None:
        last_partial = ""
        while not self._stop.is_set():
            try:
                chunk = self._audio.get(timeout=0.2)
            except queue.Empty:
                continue
            if self._recognizer.AcceptWaveform(chunk):
                text = json.loads(self._recognizer.Result()).get("text", "").strip()
                last_partial = ""
                if text:
                    self.on_final(text)
                continue

            partial = json.loads(self._recognizer.PartialResult()).get("partial", "").strip()
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
