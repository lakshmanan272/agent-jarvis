"""Splitting one spoken sentence into the sequence of commands it asks for.

People do not speak in single commands. "Open notepad and type hello" is one
breath and two actions, and without this the whole tail lands in the first
intent's capture group: `open_app` receives "notepad and type hello" as the
name of an application.

The hard part is that "and" is not reliably a separator. It joins commands in
"open notepad and type hello" and it joins *words* in "search for cats and
dogs" or "type hello and welcome". Splitting on every "and" would break more
than it fixes.

So there are two classes of separator:

  strong  "and then", "then", "after that", ";" -- these only ever sequence
          actions, so they always split.
  weak    a bare "and" -- splits only when what follows begins with a verb this
          agent actually has a command for. "and type ..." splits; "and dogs"
          does not.
"""
from __future__ import annotations

import re

# Verbs that begin a command. Deliberately a curated list rather than something
# derived from the intent patterns: patterns contain alternations, optional
# groups and lookarounds that do not reduce cleanly to "the word that starts a
# command", and a wrong entry here silently chops a sentence in half.
COMMAND_VERBS = (
    # apps and windows
    "open", "launch", "start", "run", "close", "quit", "exit", "kill",
    "restart", "reopen", "switch", "focus", "show", "minimize", "minimise",
    "maximize", "maximise", "restore", "snap", "dock",
    # keyboard and text
    "type", "write", "enter", "input", "press", "hit", "tap", "push", "hold",
    "select", "copy", "paste", "cut", "undo", "redo", "save", "delete",
    "backspace", "erase", "replace", "trim", "strip", "join", "spell",
    "insert",
    # mouse
    "click", "double", "triple", "move", "drag", "scroll", "page",
    # web
    "search", "google", "look", "play", "find", "go", "visit", "browse",
    "refresh", "reload", "bookmark",
    # system
    "volume", "mute", "unmute", "silence", "pause", "resume", "lock",
    "shutdown", "sleep", "hibernate", "screenshot", "take", "set",
    # files
    "create", "make", "new",
    # meta
    "repeat", "stop", "wait",
)

_VERB_ALT = "|".join(sorted(COMMAND_VERBS, key=len, reverse=True))

# Always a sequence marker, never part of a phrase people dictate.
_STRONG = re.compile(
    r"\s*(?:,\s*)?\b(?:and\s+then|and\s+after\s+that|after\s+that|then)\b\s*|\s*;\s*",
    re.IGNORECASE,
)

# A bare "and", but only when a command verb follows it.
_WEAK = re.compile(rf"\s+and\s+(?=(?:{_VERB_ALT})\b)", re.IGNORECASE)

MAX_STEPS = 8  # a misheard sentence should not become a 40-step macro


def split_commands(raw: str) -> list[str]:
    """Break `raw` into the commands it contains, in order.

    Returns a single-element list when there is nothing to split, so callers
    can treat the simple case and the chained case identically.
    """
    text = (raw or "").strip()
    if not text:
        return []

    parts: list[str] = []
    for chunk in _STRONG.split(text):
        parts.extend(_WEAK.split(chunk))

    steps = [p.strip(" ,.") for p in parts]
    steps = [s for s in steps if s]
    return steps[:MAX_STEPS] if steps else [text]
