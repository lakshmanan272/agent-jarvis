"""Maps a phrase to a skill and runs it.

The whole point of this module is the fast path: a normalise + regex sweep over
a few hundred compiled patterns, which costs tens of microseconds. Only when
that misses do we consider the (slow, optional, network-bound) LLM planner.
"""
from __future__ import annotations

import logging
import time

from jarvis.nlp.chain import split_commands
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
        """Run everything `raw` asks for — one command, or a sequence of them."""
        steps = split_commands(raw)
        if len(steps) > 1:
            return self._dispatch_chain(steps)
        return self._dispatch_one(steps[0] if steps else raw, _from_brain)

    def _dispatch_chain(self, steps: list[str]) -> ActionResult:
        """Run a sequence, stopping at the first step that fails.

        Stopping matters: the steps are usually dependent. "Open notepad and
        type hello" must not type into whatever was focused before, just
        because Notepad failed to launch.
        """
        started = time.perf_counter()
        done: list[str] = []
        for index, step in enumerate(steps):
            result = self._dispatch_one(step)

            if result.needs_confirm or self.ctx.pending_confirm is not None:
                # A confirmation mid-sequence would leave the remaining steps
                # in limbo across an unbounded wait, so the rest is dropped and
                # the user re-issues it after answering.
                result.detail = (
                    f"{result.detail} (stopped before {len(steps) - index - 1} "
                    "more steps)".strip()
                )
                return self._finish(result, " / ".join(steps), started, "chain")

            if not result.ok:
                return self._finish(
                    ActionResult(
                        ok=False,
                        say=f"Stopped at step {index + 1}: {result.say}",
                        detail=result.detail or result.say,
                        data={"failed_step": step, "completed": done},
                    ),
                    " / ".join(steps),
                    started,
                    "chain",
                )
            done.append(step)
            self._settle(result)

        # Speak the last step's acknowledgement rather than narrating each
        # one; a chain that worked should sound like one action, not five.
        last = self.ctx.last_result
        return self._finish(
            ActionResult(
                ok=True,
                say=(last.say if last and last.say else f"Done, {len(done)} steps."),
                detail=" -> ".join(done),
                data={"steps": done},
            ),
            " / ".join(steps),
            started,
            "chain",
        )

    def _settle(self, result: ActionResult) -> None:
        """Let a step's side effect land before the next step depends on it.

        Launching an app is asynchronous: the handler returns as soon as the
        process is spawned, but the window it creates does not exist yet. Typing
        into "whatever is focused" a millisecond later goes to the old window.
        """
        pending_window = result.data.get("await_window")
        if not pending_window:
            return
        from jarvis.skills.window import wait_for_window

        if wait_for_window(pending_window, timeout=6.0) is None:
            log.warning("window %r never appeared", pending_window)

    def _dispatch_one(self, raw: str, _from_brain: bool = False) -> ActionResult:
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
                return self._dispatch_one(plan, _from_brain=True)

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
