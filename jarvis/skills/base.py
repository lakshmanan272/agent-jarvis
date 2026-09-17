"""Skill primitives: the contract every command handler implements."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from re import Pattern


@dataclass
class ActionResult:
    ok: bool
    say: str = ""                     # short line spoken back to the user
    detail: str = ""                  # longer text for the HUD / log only
    data: dict = field(default_factory=dict)
    needs_confirm: str = ""           # non-empty => ask before really doing it

    @classmethod
    def fail(cls, say: str, detail: str = "") -> ActionResult:
        return cls(ok=False, say=say, detail=detail or say)


@dataclass
class Context:
    """Mutable per-session state handed to every skill."""
    config: object
    speak: Callable[[str], None]
    last_command: str = ""
    last_result: ActionResult | None = None
    last_target: str = ""             # e.g. the app most recently opened
    pending_confirm: Callable[[], ActionResult] | None = None
    variables: dict = field(default_factory=dict)


Handler = Callable[..., ActionResult]


@dataclass
class Intent:
    name: str
    patterns: list[Pattern[str]]
    handler: Handler
    priority: int = 0                 # higher wins when two intents both match
    description: str = ""
    examples: tuple[str, ...] = ()
    destructive: bool = False
    # True when a captured group is literal content the user dictated (text to
    # type, a search query, a filename) rather than a control word. The router
    # then re-extracts the groups from lightly-cleaned text so capitalisation,
    # numbers and ordinary words like "please" survive intact.
    verbatim: bool = False

    def match(self, text: str) -> re.Match[str] | None:
        for pattern in self.patterns:
            m = pattern.search(text)
            if m:
                return m
        return None


REGISTRY: list[Intent] = []


def intent(
    *patterns: str,
    name: str = "",
    priority: int = 0,
    description: str = "",
    examples: tuple[str, ...] = (),
    destructive: bool = False,
    verbatim: bool = False,
) -> Callable[[Handler], Handler]:
    """Register a handler for one or more regex patterns.

    Patterns are matched case-insensitively against normalised text (lowercase,
    punctuation stripped, filler words removed) — so write them in plain lower
    case with no trailing punctuation.

    Set `verbatim=True` when a captured group is literal content the user
    dictated. The router then feeds the handler groups taken from the original
    phrasing, so "type Just Do It" types `Just Do It` and not `do it`.
    """

    def decorator(fn: Handler) -> Handler:
        REGISTRY.append(
            Intent(
                name=name or fn.__name__,
                patterns=[re.compile(p, re.IGNORECASE) for p in patterns],
                handler=fn,
                priority=priority,
                description=description or (fn.__doc__ or "").strip().splitlines()[0]
                if fn.__doc__
                else description,
                examples=examples,
                destructive=destructive,
                verbatim=verbatim,
            )
        )
        return fn

    return decorator
