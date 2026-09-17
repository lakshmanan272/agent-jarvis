"""Optional LLM fallback.

The local router handles everything it has a pattern for in microseconds. This
module only sees phrases that missed — its job is to rewrite loose natural
language into one canonical command the router *does* know, not to invent new
abilities. Keeping it to a rewrite means one small, cheap, bounded call.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.brain")

# How many candidate commands to show the model. Enough that the right one
# is almost always present, few enough to stay inside a free tier's budget.
SHORTLIST = 18

SYSTEM_PROMPT = """You translate a user's spoken request into exactly ONE command \
from the list below. Reply with the command text only: no quotes, no explanation, \
no punctuation at the end. If nothing in the list fits, reply exactly: UNKNOWN

Available commands (with example phrasings):
{catalog}"""


class Brain:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._phrasing_cache: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        if not self.cfg.enabled:
            return False
        if self.cfg.provider in ("anthropic", "openai"):
            return bool(self.cfg.key())
        return True  # ollama needs no credential

    def _phrasings(self, intents) -> list[tuple[str, str]]:
        """(example, description) for every intent, computed once."""
        if not self._phrasing_cache:
            self._phrasing_cache = [
                (
                    item.examples[0] if item.examples else item.name.replace("_", " "),
                    item.description or item.name,
                )
                for item in intents
            ]
        return self._phrasing_cache

    def _build_catalog(self, intents, text: str = "") -> str:
        """The menu shown to the model, narrowed to what could plausibly fit.

        All 91 commands cost about 1100 tokens a call. On a free tier rated at
        8000 tokens a minute that is roughly seven commands before everything
        starts coming back 429 — which is exactly what happened when this was
        measured. Ranking by similarity and sending only the closest
        `SHORTLIST` cuts it several-fold, and it improves the answer too: the
        model chooses between a dozen plausible commands instead of
        skim-reading ninety mostly irrelevant ones.
        """
        entries = self._phrasings(intents)
        if not text:
            return "\n".join(f"- {ex}  ({desc})" for ex, desc in entries)
        try:
            from rapidfuzz import fuzz
        except ImportError:
            entries = entries[:SHORTLIST]
        else:
            entries = sorted(
                entries,
                key=lambda e: max(
                    fuzz.token_set_ratio(text, e[0]),
                    fuzz.token_set_ratio(text, e[1]),
                ),
                reverse=True,
            )[:SHORTLIST]
        return "\n".join(f"- {ex}  ({desc})" for ex, desc in entries)

    def plan(self, text: str, intents) -> str | None:
        """Return a router-runnable command string, or None."""
        try:
            import httpx
        except ImportError:
            return None
        prompt = SYSTEM_PROMPT.format(catalog=self._build_catalog(intents, text))
        try:
            if self.cfg.provider == "anthropic":
                reply = self._anthropic(httpx, prompt, text)
            elif self.cfg.provider == "openai":
                reply = self._openai(httpx, prompt, text)
            else:
                reply = self._ollama(httpx, prompt, text)
        except Exception as exc:
            log.warning("brain call failed: %s", exc)
            return None
        reply = (reply or "").strip().strip('"').strip()
        if not reply or reply.upper().startswith("UNKNOWN"):
            return None
        # Guard against the model echoing the prompt or waffling.
        if len(reply) > 120 or "\n" in reply:
            return None
        return reply

    def _anthropic(self, httpx, system: str, text: str) -> str:
        key = self.cfg.key()
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.cfg.model,
                "max_tokens": 64,
                "system": system,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=self.cfg.timeout_s,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def _openai(self, httpx, system: str, text: str) -> str:
        """Any service speaking the OpenAI chat format.

        One method covers Groq, OpenRouter, Together, Gemini's compatibility
        endpoint and a local llama.cpp server -- they differ only in `base_url`
        and the model name, so adding a provider is configuration, not code.
        """
        response = httpx.post(
            f"{self.cfg.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.cfg.key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.cfg.model,
                "max_tokens": 64,
                "temperature": 0,  # this is a lookup, not a conversation
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
            timeout=self.cfg.timeout_s,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        return choices[0]["message"]["content"] if choices else ""

    def _ollama(self, httpx, system: str, text: str) -> str:
        response = httpx.post(
            f"{self.cfg.ollama_url}/api/chat",
            json={
                "model": self.cfg.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
            timeout=self.cfg.timeout_s,
        )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")
