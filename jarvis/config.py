"""Runtime configuration. Loaded once at startup, hot-reloadable from disk."""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "jarvis" / "data"
USER_DIR = Path(os.environ.get("JARVIS_HOME", Path.home() / ".jarvis"))
MODEL_DIR = USER_DIR / "models"
CONFIG_PATH = USER_DIR / "config.json"

# Offline English models, both streaming. "fast" loads in half a second and is
# enough for short commands in a quiet room; "accurate" is a wider-graph model
# that mishears far less with background noise or an accent, at a larger
# one-time download and about three times the memory.
SPEECH_MODELS = {
    "fast": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    "accurate": "https://alphacephei.com/vosk/models/vosk-model-en-us-0.22-lgraph.zip",
}
VOSK_SMALL_URL = SPEECH_MODELS["fast"]  # kept for callers that import it by name


@dataclass
class SpeechConfig:
    enabled: bool = True
    engine: str = "vosk"              # "vosk" | "none"
    # "fast" (~40 MB) or "accurate" (~128 MB). Switching re-downloads once.
    accuracy: str = "fast"
    # Set to override `accuracy` with any Vosk model archive URL.
    model_url: str = ""
    sample_rate: int = 16000
    block_ms: int = 30                # audio chunk size fed to the recogniser
    device: int | None = None         # input device index, None = default mic
    wake_words: list[str] = field(default_factory=lambda: ["jarvis", "hey jarvis"])
    # An acoustic wake-word detector, so the recogniser can stay asleep until
    # it is actually addressed. "none" falls back to matching `wake_words` in
    # the transcript, which needs everything transcribed first.
    wake_engine: str = "openwakeword"     # "openwakeword" | "none"
    wake_model: str = "hey_jarvis"
    wake_threshold: float = 0.5
    # Once woken, keep listening for this long so follow-ups need no wake word.
    conversation_timeout_s: float = 12.0
    always_on: bool = False           # True = act on every phrase, no wake word
    partial_dispatch: bool = True     # act on stable partials for lower latency
    # How much quiet ends an utterance. Vosk's own endpointer waits far longer
    # because it is tuned for dictation; for commands this is the single
    # biggest slice of end-to-end latency. Raise it if words get cut off.
    endpoint_silence_ms: int = 180

    def resolve_model_url(self) -> str:
        """The archive to fetch: an explicit override, else the accuracy tier."""
        return self.model_url or SPEECH_MODELS.get(
            self.accuracy, SPEECH_MODELS["fast"]
        )


@dataclass
class VoiceOutConfig:
    enabled: bool = True
    rate: int = 200
    volume: float = 0.9
    voice_hint: str = ""              # substring match on installed SAPI voices
    max_chars: int = 240              # never monologue; Jarvis is terse


@dataclass
class ControlConfig:
    # pyautogui pause between primitives. 0 = as fast as the OS accepts.
    action_pause_s: float = 0.0
    move_duration_s: float = 0.0      # instant cursor teleport
    # Fallback typing only. The normal path injects a whole sentence in one
    # batched SendInput call and ignores both of these; they apply when that is
    # refused (an elevated window, the secure desktop, a non-Windows host), and
    # the interval is non-zero because Electron/WebView apps drop keystrokes
    # fed faster than their renderer polls.
    type_interval_s: float = 0.01
    paste_threshold: int = 12
    failsafe: bool = True             # slam mouse to a corner to abort
    confirm_destructive: bool = True  # ask before shutdown / close-all / delete


@dataclass
class HotkeyConfig:
    push_to_talk: str = "ctrl+alt+j"  # hold-free toggle for a single command
    text_console: str = "ctrl+alt+k"  # focus the HUD text box
    panic_stop: str = "ctrl+alt+x"    # abort whatever is running
    toggle_mute: str = "ctrl+alt+m"


@dataclass
class BrainConfig:
    """Optional LLM fallback for phrases the local router cannot resolve."""

    enabled: bool = False
    # "anthropic" | "ollama" | "openai" -- the last covers every service that
    # speaks the OpenAI chat format, which is Groq, OpenRouter, Together,
    # Gemini's compatibility endpoint and a local llama.cpp server.
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    # Read from the environment first. `api_key` is the fallback, and lives in
    # the user's own config file outside the repository -- a key must never be
    # committed.
    api_key_env: str = "ANTHROPIC_API_KEY"
    api_key: str = ""
    base_url: str = ""                # for provider "openai"
    ollama_url: str = "http://127.0.0.1:11434"
    timeout_s: float = 8.0

    # A second service to try when the first one refuses. Measured need: a
    # free tier rate-limits, and 15 calls in one session came back 429 with
    # nothing to fall back to, so every one of them answered "I don't know
    # how to ...". Left blank there is no fallback and behaviour is unchanged.
    fallback_provider: str = ""        # "openai" | "anthropic" | "ollama"
    fallback_model: str = ""
    fallback_base_url: str = ""
    fallback_api_key_env: str = ""
    fallback_api_key: str = ""

    def key(self) -> str:
        import os

        return os.environ.get(self.api_key_env, "") or self.api_key

    def fallback(self) -> BrainConfig | None:
        """The fallback as a config in its own right, or None.

        Returning a whole BrainConfig rather than a special case means the
        calling code runs the fallback through exactly the same path as the
        primary -- there is no second, less-tested way to reach a model.
        """
        if not self.fallback_provider:
            return None
        return BrainConfig(
            enabled=True,
            provider=self.fallback_provider,
            model=self.fallback_model or self.model,
            api_key_env=self.fallback_api_key_env,
            api_key=self.fallback_api_key,
            base_url=self.fallback_base_url,
            ollama_url=self.ollama_url,
            timeout_s=self.timeout_s,
        )


@dataclass
class EditorConfig:
    """The code editor Jarvis drives from the command line.

    Antigravity is a VS Code fork, so its CLI takes the same arguments; the
    same settings work for `code` or `codium` by changing `cli`. Left blank,
    the path is discovered on first use and not written back -- a machine
    where the editor moves should keep working without an edit here.
    """

    cli: str = ""
    # Where a bare project name is looked up, so "open agent jarvis in
    # antigravity" does not need the full path spoken aloud.
    project_roots: tuple[str, ...] = ()


@dataclass
class UIConfig:
    enabled: bool = True
    # Nearly solid. At 0.92 the window behind bled through the text enough
    # to make a heard phrase hard to read, which defeats the point of
    # showing it.
    opacity: float = 0.97
    accent: str = "#22d3ee"
    width: int = 560
    height: int = 196
    margin: int = 28
    corner: str = "bottom-right"


@dataclass
class Config:
    speech: SpeechConfig = field(default_factory=SpeechConfig)
    voice_out: VoiceOutConfig = field(default_factory=VoiceOutConfig)
    control: ControlConfig = field(default_factory=ControlConfig)
    hotkeys: HotkeyConfig = field(default_factory=HotkeyConfig)
    brain: BrainConfig = field(default_factory=BrainConfig)
    ui: UIConfig = field(default_factory=UIConfig)
    editor: EditorConfig = field(default_factory=EditorConfig)
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        path = path or CONFIG_PATH
        cfg = cls()
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return cfg
            _merge(cfg, raw)
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def _merge(obj, raw: dict) -> None:
    """Shallow-merge a JSON dict onto a nested dataclass instance."""
    for key, value in raw.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if hasattr(current, "__dataclass_fields__") and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(obj, key, value)


USER_DIR.mkdir(parents=True, exist_ok=True)
MODEL_DIR.mkdir(parents=True, exist_ok=True)
