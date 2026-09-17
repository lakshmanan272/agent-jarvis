"""Maps a phrase to a skill and runs it.

The whole point of this module is the fast path: a normalise + regex sweep over
a few hundred compiled patterns, which costs tens of microseconds. Only when
that misses do we consider the (slow, optional, network-bound) LLM planner.
"""
from __future__ import annotations

import logging
import time

from jarvis.nlp.matcher import light_clean, normalize, strip_lead_in
from jarvis.skills import load_all_skills
from jarvis.skills.base import REGISTRY, ActionResult, Context, Intent

log = logging.getLogger("jarvis.router")

# Spoken affirmations that resolve a pending confirmation. Matched against the
# raw phrase, not the normalised one: `normalize` treats "okay" as filler and
# would erase the entire answer.
_YES = ("yes", "yeah", "yep", "yup", "confirm", "do it", "go ahead", "sure", "ok", "okay")
_NO = ("no", "nope", "cancel", "stop", "abort", "never mind", "nevermind", "don't")


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

    def dispatch(self, raw: str, _from_brain: bool = False) -> ActionResult:
        started = time.perf_counter()
        text = normalize(raw)

        pending = self.ctx.pending_confirm
        if pending is not None:
            self.ctx.pending_confirm = None
            # Matched against the barely-touched phrase: "ok" and "okay" are
            # filler words everywhere else, so both `normalize` and
            # `light_clean` erase them -- and erasing the answer to "are you
            # sure?" would silently drop the user's "okay".
            answer = strip_lead_in(raw).strip(" .,!?").lower()
            if answer.startswith(_YES):
                return self._finish(pending(), text, started, "confirm")
            if answer.startswith(_NO):
                return self._finish(
                    ActionResult(ok=True, say="Cancelled."), text, started, "confirm"
                )
            # Anything else: treat the confirmation as declined and route normally.

        if not text:
            return self._finish(
                ActionResult.fail("I didn't catch that."), text, started
            )

        for intent in self._intents:
            match = intent.match(text)
            if match is None:
                continue
            kwargs = self._kwargs_for(intent, match, raw)
            # Handlers may inspect the command they were invoked for, so this
            # has to be set before the call, not after it in `_finish`.
            self.ctx.last_command = text
            try:
                result = intent.handler(self.ctx, **kwargs)
            except Exception as exc:
                log.exception("intent %s failed", intent.name)
                result = ActionResult.fail(
                    "That didn't work.", f"{intent.name}: {exc.__class__.__name__}: {exc}"
                )
            if result.needs_confirm:
                self.ctx.pending_confirm = lambda i=intent, k=kwargs: i.handler(
                    self.ctx, _confirmed=True, **k
                )
                result = ActionResult(ok=True, say=result.needs_confirm)
            return self._finish(result, text, started, intent.name)

        # The brain only ever rewrites a phrase into a command we already have,
        # so its output gets one pass through the router and no second opinion:
        # without this guard a rewrite that also misses would loop back here.
        if not _from_brain and self.brain is not None and self.brain.available:
            plan = self.brain.plan(text, self._intents)
            if plan:
                log.info("brain resolved %r -> %r", text, plan)
                return self.dispatch(plan, _from_brain=True)

        return self._finish(
            ActionResult.fail(f"I don't know how to {text}."), text, started
        )

    def _kwargs_for(self, intent: Intent, match, raw: str) -> dict:
        """Handler arguments, taken from the original phrasing where it matters.

        For a `verbatim` intent the same pattern is re-run against lightly
        cleaned text, which keeps capitalisation, spelled-out numbers and words
        like "please" that `normalize` would otherwise strip out of the payload.
        If that second match fails the normalised groups still apply, so a
        command never breaks outright over this.
        """
        if not intent.verbatim:
            return _kwargs(match)
        literal = intent.match(light_clean(raw))
        return _kwargs(literal) if literal is not None else _kwargs(match)

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
