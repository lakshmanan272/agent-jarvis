"""Optional LLM fallback.

The local router handles everything it has a pattern for in microseconds. This
module only sees phrases that missed — its job is to rewrite loose natural
language into one canonical command the router *does* know, not to invent new
abilities. Keeping it to a rewrite means one small, cheap, bounded call.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("jarvis.brain")

SYSTEM_PROMPT = """You translate a user's spoken request into exactly ONE command \
from the list below. Reply with the command text only: no quotes, no explanation, \
no punctuation at the end. If nothing in the list fits, reply exactly: UNKNOWN

Available commands (with example phrasings):
{catalog}"""


class Brain:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._catalog = ""

    @property
    def available(self) -> bool:
        if not self.cfg.enabled:
            return False
        if self.cfg.provider == "anthropic":
            return bool(os.environ.get(self.cfg.api_key_env))
        return True

    def _build_catalog(self, intents) -> str:
        if self._catalog:
            return self._catalog
        lines = []
        for item in intents:
            example = item.examples[0] if item.examples else item.name.replace("_", " ")
            lines.append(f"- {example}  ({item.description or item.name})")
        self._catalog = "\n".join(lines)
        return self._catalog

    def plan(self, text: str, intents) -> str | None:
        """Return a router-runnable command string, or None."""
        try:
            import httpx
        except ImportError:
            return None
        prompt = SYSTEM_PROMPT.format(catalog=self._build_catalog(intents))
        try:
            if self.cfg.provider == "anthropic":
                reply = self._anthropic(httpx, prompt, text)
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
        key = os.environ.get(self.cfg.api_key_env, "")
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
