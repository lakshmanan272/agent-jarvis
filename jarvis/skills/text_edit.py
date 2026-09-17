"""Dictation helpers: punctuation, casing, line control and clipboard rewrites."""
from __future__ import annotations

import re

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

# Spoken punctuation, so dictation doesn't need a keyboard for symbols.
SYMBOLS = {
    "comma": ",",
    "period": ".",
    "full stop": ".",
    "dot": ".",
    "question mark": "?",
    "exclamation mark": "!",
    "exclamation point": "!",
    "colon": ":",
    "semicolon": ";",
    "dash": "-",
    "hyphen": "-",
    "underscore": "_",
    "slash": "/",
    "backslash": "\\",
    "open paren": "(",
    "close paren": ")",
    "open bracket": "[",
    "close bracket": "]",
    "open brace": "{",
    "close brace": "}",
    "quote": '"',
    "single quote": "'",
    "apostrophe": "'",
    "at sign": "@",
    "hash": "#",
    "hashtag": "#",
    "dollar sign": "$",
    "percent sign": "%",
    "ampersand": "&",
    "asterisk": "*",
    "star": "*",
    "plus sign": "+",
    "equals": "=",
    "less than": "<",
    "greater than": ">",
    "pipe": "|",
    "tilde": "~",
    "backtick": "`",
}

_SYMBOL_ALT = "|".join(sorted(map(re.escape, SYMBOLS), key=len, reverse=True))


@intent(
    rf"^(?:insert\s+)?(?P<symbol>{_SYMBOL_ALT})$",
    name="insert_symbol",
    priority=4,
    description="Type a spoken punctuation mark",
    examples=("comma", "question mark", "open paren"),
)
def do_symbol(ctx, symbol: str = "", **_) -> ActionResult:
    char = SYMBOLS.get(symbol.strip())
    if char is None:
        return ActionResult.fail(f"No symbol for {symbol}.")
    act.type_text(char, paste_threshold=999)  # always keystroke: it's one char
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:new\s+line|newline|line\s+break|next\s+line)$",
    name="newline",
    priority=6,
    description="Insert a line break",
)
def do_newline(ctx, **_) -> ActionResult:
    act.press("enter")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:new\s+paragraph|paragraph\s+break)$",
    name="paragraph",
    examples=('new paragraph',),
    priority=6,
    description="Insert a blank line",
)
def do_paragraph(ctx, **_) -> ActionResult:
    act.press("enter", presses=2)
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:tab|indent)$", name="tab", priority=6, description="Insert a tab"
)
def do_tab(ctx, **_) -> ActionResult:
    act.press("tab")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:space|space\s+bar)$", name="space", priority=6, description="Insert a space"
)
def do_space(ctx, **_) -> ActionResult:
    act.press("space")
    return ActionResult(ok=True, say="")


def _transform_selection(fn) -> ActionResult:
    """Copy the selection, rewrite it, paste it back.

    Going through the clipboard is the only app-agnostic way to read what the
    user has highlighted — there is no cross-application "get selected text".
    """
    saved = act.read_clipboard()
    act.write_clipboard("")
    act.hotkey("ctrl", "c")
    import time

    time.sleep(0.08)  # the copy is asynchronous; give the clipboard a moment
    selected = act.read_clipboard()
    if not selected:
        act.write_clipboard(saved)
        return ActionResult.fail("Nothing selected.")
    act.write_clipboard(fn(selected))
    act.hotkey("ctrl", "v")
    return ActionResult(ok=True, say="", detail=f"rewrote {len(selected)} chars")


@intent(
    r"^(?:make\s+(?:it|that|this)\s+)?(?P<case>upper\s*case|lower\s*case|title\s*case|"
    r"capitali[sz]e)(?:\s+(?:it|that|this))?$",
    name="change_case",
    priority=7,
    description="Change the case of the selected text",
    examples=("uppercase", "make it title case"),
)
def do_change_case(ctx, case: str = "", **_) -> ActionResult:
    case = case.replace(" ", "")
    fns = {
        "uppercase": str.upper,
        "lowercase": str.lower,
        "titlecase": str.title,
        "capitalize": str.capitalize,
        "capitalise": str.capitalize,
    }
    fn = fns.get(case)
    if fn is None:
        return ActionResult.fail(f"Unknown case {case}.")
    return _transform_selection(fn)


@intent(
    r"^(?:trim|strip)(?:\s+(?:it|that|whitespace))?$",
    name="trim_selection",
    examples=('trim',),
    description="Strip whitespace from the selection",
)
def do_trim(ctx, **_) -> ActionResult:
    return _transform_selection(lambda s: s.strip())


@intent(
    r"^(?:join\s+lines|remove\s+(?:the\s+)?line\s+breaks)$",
    name="join_lines",
    description="Collapse the selection onto one line",
)
def do_join_lines(ctx, **_) -> ActionResult:
    return _transform_selection(lambda s: " ".join(s.split()))


@intent(
    r"^replace\s+(?P<old>.+?)\s+with\s+(?P<new>.+)$",
    name="find_replace",
    examples=('replace cat with dog',),
    verbatim=True,
    priority=8,
    description="Find and replace within the selection",
)
def do_replace(ctx, old: str = "", new: str = "", **_) -> ActionResult:
    return _transform_selection(lambda s: s.replace(old, new))


@intent(
    r"^(?:spell|spell\s+out)\s+(?P<word>\w+)$",
    name="spell_word",
    verbatim=True,
    priority=8,
    description="Type a word letter by letter",
)
def do_spell(ctx, word: str = "", **_) -> ActionResult:
    act.type_text(word, paste_threshold=999)
    return ActionResult(ok=True, say="")
