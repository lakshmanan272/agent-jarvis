"""Maps a phrase to a skill and runs it.

The whole point of this module is the fast path: a normalise + regex sweep over
a few hundred compiled patterns, which costs tens of microseconds. Only when
that misses do we consider the (slow, optional, network-bound) LLM planner.
"""
from __future__ import annotations

import logging
import time

from jarvis.nlp.matcher import normalize
from jarvis.skills import load_all_skills
from jarvis.skills.base import REGISTRY, ActionResult, Context, Intent

log = logging.getLogger("jarvis.router")

# Spoken affirmations that resolve a pending confirmation.
_YES = {"yes", "yeah", "yep", "confirm", "do it", "go ahead", "sure", "ok", "okay"}
_NO = {"no", "nope", "cancel", "stop", "abort", "never mind", "nevermind"}


class Router:
    def __init__(self, ctx: Context, brain=None) -> None:
        load_all_skills()
        # Sort once so dispatch is a straight scan: specific intents first.
        self._intents: list[Intent] = sorted(
            REGISTRY, key=lambda i: (-i.priority, -_specificity(i))
        )
        self.ctx = ctx
        self.brain = brain
        log.info("router ready with %d intents", len(self._intents))

    @property
    def intents(self) -> list[Intent]:
        return self._intents

    def dispatch(self, raw: str) -> ActionResult:
        started = time.perf_counter()
        text = normalize(raw)
        if not text:
            return ActionResult.fail("I didn't catch that.")

        pending = self.ctx.pending_confirm
        if pending is not None:
            self.ctx.pending_confirm = None
            if text in _YES or any(text.startswith(y) for y in _YES):
                return self._finish(pending(), text, started)
            if text in _NO or any(text.startswith(n) for n in _NO):
                return self._finish(ActionResult(ok=True, say="Cancelled."), text, started)
            # Anything else: treat the confirmation as declined and route normally.

        for intent in self._intents:
            match = intent.match(text)
            if match is None:
                continue
            try:
                result = intent.handler(self.ctx, **_kwargs(match))
            except Exception as exc:
                log.exception("intent %s failed", intent.name)
                result = ActionResult.fail(
                    "That didn't work.", f"{intent.name}: {exc.__class__.__name__}: {exc}"
                )
            if result.needs_confirm:
                self.ctx.pending_confirm = lambda i=intent, m=match: i.handler(
                    self.ctx, _confirmed=True, **_kwargs(m)
                )
                result = ActionResult(ok=True, say=result.needs_confirm)
            return self._finish(result, text, started, intent.name)

        if self.brain is not None and self.brain.available:
            plan = self.brain.plan(text, self._intents)
            if plan:
                log.info("brain resolved %r -> %r", text, plan)
                return self.dispatch(plan)

        return self._finish(
            ActionResult.fail(f"I don't know how to {text}."), text, started
        )

    def _finish(
        self, result: ActionResult, text: str, started: float, intent: str = "-"
    ) -> ActionResult:
        elapsed_ms = (time.perf_counter() - started) * 1000
        result.data.setdefault("elapsed_ms", round(elapsed_ms, 1))
        result.data.setdefault("intent", intent)
        self.ctx.last_command = text
        self.ctx.last_result = result
        log.info("%-22s %-40r %6.1f ms  ok=%s", intent, text, elapsed_ms, result.ok)
        return result


def _kwargs(match) -> dict:
    """Named regex groups become handler keyword arguments."""
    return {k: (v.strip() if isinstance(v, str) else v) for k, v in match.groupdict().items()}


def _specificity(intent: Intent) -> int:
    """Longer, more literal patterns should be tried before catch-alls."""
    return max(len(p.pattern) for p in intent.patterns)
