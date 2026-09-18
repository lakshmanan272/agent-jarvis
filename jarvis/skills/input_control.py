"""Direct mouse and keyboard control: click, type, select, scroll, press."""
from __future__ import annotations

import re

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

_CLICK_COUNT = {"": 1, "single": 1, "double": 2, "triple": 3}
_BUTTONS = {"left": "left", "right": "right", "middle": "middle"}

# Words that mean "repeat N times" trailing a command, e.g. "press tab 3 times".
_TIMES = re.compile(r"\s+(\d+)\s*(?:times?|x)$")
_CHORD_SPLIT = re.compile(r"\s+(?:plus|and)\s+|\s*\+\s*|\s+")


def _split_times(value: str) -> tuple[str, int]:
    m = _TIMES.search(value or "")
    if not m:
        return (value or "").strip(), 1
    return value[: m.start()].strip(), max(1, min(int(m.group(1)), 50))


def _chord(keys: str) -> list[str]:
    """Split "ctrl shift s" / "ctrl+s" / "ctrl plus s" into key names."""
    return [p for p in _CHORD_SPLIT.split(keys.strip()) if p]


@intent(
    r"^(?P<kind>double|triple|single)?\s*(?:mouse\s*)?click"
    r"(?:\s+(?P<button>left|right|middle))?(?:\s+(?:button|here|there))?$",
    r"^(?P<button>right|middle)\s+click$",
    name="click",
    priority=5,
    description="Click at the current cursor position",
    examples=("click", "double click", "right click"),
)
def do_click(ctx, kind: str = "", button: str = "", **_) -> ActionResult:
    clicks = _CLICK_COUNT.get((kind or "").strip(), 1)
    btn = _BUTTONS.get((button or "left").strip(), "left")
    act.click(button=btn, clicks=clicks)
    return ActionResult(ok=True, say="")


@intent(
    r"^click(?:\s+(?:at|on))?\s+(?P<x>\d+)[\s,]+(?P<y>\d+)$",
    name="click_at",
    examples=('click at 400 300',),
    priority=7,
    description="Click at exact screen coordinates",
)
def do_click_at(ctx, x: str = "0", y: str = "0", **_) -> ActionResult:
    return _goto(int(x), int(y), click=True)


@intent(
    r"^(?:move|go)\s+(?:mouse\s+|cursor\s+)?to\s+(?P<x>\d+)[\s,]+(?P<y>\d+)$",
    name="move_to",
    examples=('move to 400 300',),
    priority=7,
    description="Move the cursor to exact screen coordinates",
)
def do_move_to(ctx, x: str = "0", y: str = "0", **_) -> ActionResult:
    return _goto(int(x), int(y), click=False)


def _goto(px: int, py: int, click: bool) -> ActionResult:
    width, height = act.screen_size()
    if not (0 <= px < width and 0 <= py < height):
        return ActionResult.fail(f"{px},{py} is off screen.")
    act.move(px, py)
    if click:
        act.click()
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:move|shift|nudge)\s+(?:the\s+)?(?:mouse|cursor)\s+"
    r"(?P<direction>up|down|left|right)"
    r"(?:\s+(?:by\s+)?(?P<amount>\d+))?(?:\s*(?:pixels?|px))?$",
    name="move_mouse",
    examples=('move the mouse right 200',),
    description="Nudge the cursor in a direction",
)
def do_move_mouse(
    ctx, direction: str = "right", amount: str | None = None, **_
) -> ActionResult:
    step = int(amount) if amount else 100
    dx, dy = {
        "up": (0, -step),
        "down": (0, step),
        "left": (-step, 0),
        "right": (step, 0),
    }[direction]
    act.move_relative(dx, dy)
    return ActionResult(ok=True, say="")


@intent(
    r"^drag(?:\s+to)?\s+(?P<x>\d+)[\s,]+(?P<y>\d+)$",
    name="drag",
    examples=('drag to 900 500',),
    description="Drag from the cursor to coordinates",
)
def do_drag(ctx, x: str = "0", y: str = "0", **_) -> ActionResult:
    act.drag_to(int(x), int(y))
    return ActionResult(ok=True, say="Dragged.")


@intent(
    r"^(?:type|write|enter|input)\s+(?P<text>.+)$",
    name="type_text",
    verbatim=True,
    priority=1,
    description="Type text into the focused field",
    examples=("type hello world", "write my name is arun"),
)
def do_type(ctx, text: str = "", **_) -> ActionResult:
    if not text:
        return ActionResult.fail("Type what?")
    cfg = ctx.config.control
    act.type_text(
        text, paste_threshold=cfg.paste_threshold, interval=cfg.type_interval_s
    )
    return ActionResult(ok=True, say="", detail=f"typed {len(text)} chars")


@intent(
    r"^(?:press|hit|tap|push)\s+(?P<keys>.+)$",
    name="press_key",
    priority=2,
    description="Press a key or key combination",
    examples=("press enter", "press ctrl s", "press tab 3 times"),
)
def do_press(ctx, keys: str = "", **_) -> ActionResult:
    keys, times = _split_times(keys)
    parts = _chord(keys)
    if not parts:
        return ActionResult.fail("Press what?")
    if len(parts) > 1:
        for _i in range(times):
            act.hotkey(*parts)
    else:
        act.press(parts[0], presses=times)
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:scroll|page)\s+(?P<direction>up|down|left|right|top|bottom)"
    r"(?:\s+(?:by\s+)?(?P<amount>\d+))?(?:\s*(?:lines?|clicks?|pixels?))?$",
    r"^scroll\s+to\s+(?:the\s+)?(?P<direction>top|bottom)$",
    name="scroll",
    description="Scroll the window under the cursor",
    examples=("scroll down", "scroll up 5", "scroll to bottom"),
)
def do_scroll(
    ctx, direction: str = "down", amount: str | None = None, **_
) -> ActionResult:
    if direction == "top":
        act.hotkey("ctrl", "home")
        return ActionResult(ok=True, say="")
    if direction == "bottom":
        act.hotkey("ctrl", "end")
        return ActionResult(ok=True, say="")
    # One wheel notch is 120 units on Windows; 3 notches is a comfortable step.
    units = (int(amount) if amount else 3) * 120
    if direction in ("up", "down"):
        act.scroll(units if direction == "up" else -units)
    else:
        act.scroll(units if direction == "right" else -units, horizontal=True)
    return ActionResult(ok=True, say="")


@intent(r"^select\s+all$", name="select_all", priority=6)
def do_select_all(ctx, **_) -> ActionResult:
    """Select everything in the focused field."""
    act.hotkey("ctrl", "a")
    return ActionResult(ok=True, say="")


@intent(
    r"^select\s+(?:the\s+)?(?P<unit>word|line|paragraph|to\s+end|to\s+start)$",
    name="select_unit",
    examples=('select the line',),
    priority=5,
    description="Select the word, line or paragraph at the caret",
)
def do_select_unit(ctx, unit: str = "line", **_) -> ActionResult:
    unit = unit.replace(" ", "")
    if unit == "word":
        act.hotkey("ctrl", "shift", "left")
    elif unit == "line":
        act.press("home")
        act.hotkey("shift", "end")
    elif unit == "paragraph":
        act.hotkey("ctrl", "shift", "down")
    elif unit == "toend":
        act.hotkey("ctrl", "shift", "end")
    else:
        act.hotkey("ctrl", "shift", "home")
    return ActionResult(ok=True, say="")


@intent(
    r"^select\s+(?P<count>\d+)\s+(?P<unit>words?|lines?|characters?|chars?)"
    r"(?:\s+(?P<direction>left|right|up|down))?$",
    name="select_count",
    examples=('select 3 words',),
    priority=6,
    description="Select N words, lines or characters",
)
def do_select_count(
    ctx,
    count: str = "1",
    unit: str = "word",
    direction: str | None = None,
    **_,
) -> ActionResult:
    n = max(1, min(int(count), 200))
    unit = unit.rstrip("s")
    if unit in ("char", "character"):
        arrow = direction or "right"
        for _i in range(n):
            act.hotkey("shift", arrow)
    elif unit == "word":
        arrow = direction or "right"
        for _i in range(n):
            act.hotkey("ctrl", "shift", arrow)
    else:
        arrow = direction or "down"
        for _i in range(n):
            act.hotkey("shift", arrow)
    return ActionResult(ok=True, say="")


@intent(r"^copy(?:\s+(?:that|this|it))?$", name="copy", priority=5)
def do_copy(ctx, **_) -> ActionResult:
    """Copy the selection."""
    act.hotkey("ctrl", "c")
    return ActionResult(ok=True, say="")


@intent(r"^paste(?:\s+(?:that|this|it))?$", name="paste", priority=5)
def do_paste(ctx, **_) -> ActionResult:
    """Paste the clipboard."""
    act.hotkey("ctrl", "v")
    return ActionResult(ok=True, say="")


@intent(r"^cut(?:\s+(?:that|this|it))?$", name="cut", priority=5)
def do_cut(ctx, **_) -> ActionResult:
    """Cut the selection."""
    act.hotkey("ctrl", "x")
    return ActionResult(ok=True, say="")


@intent(r"^undo(?:\s+that)?$", name="undo", priority=5)
def do_undo(ctx, **_) -> ActionResult:
    """Undo the last edit."""
    act.hotkey("ctrl", "z")
    return ActionResult(ok=True, say="")


@intent(r"^redo(?:\s+that)?$", name="redo", priority=5)
def do_redo(ctx, **_) -> ActionResult:
    """Redo the last undone edit."""
    act.hotkey("ctrl", "y")
    return ActionResult(ok=True, say="")


@intent(r"^save(?:\s+(?:it|this|file|the\s+file))?$", name="save", priority=5)
def do_save(ctx, **_) -> ActionResult:
    """Save the current document."""
    act.hotkey("ctrl", "s")
    return ActionResult(ok=True, say="Saved.")


@intent(
    r"^(?:delete|backspace|erase)(?:\s+(?P<count>\d+))?"
    r"(?:\s+(?P<unit>characters?|chars?|words?|lines?))?$",
    name="delete",
    description="Delete backwards by characters, words or lines",
)
def do_delete(
    ctx, count: str | None = None, unit: str | None = None, **_
) -> ActionResult:
    n = max(1, min(int(count), 500)) if count else 1
    unit = (unit or "character").rstrip("s")
    if unit == "word":
        for _i in range(n):
            act.hotkey("ctrl", "backspace")
    elif unit == "line":
        for _i in range(n):
            act.press("home")
            act.hotkey("shift", "end")
            act.press("delete")
            act.press("backspace")
    else:
        act.press("backspace", presses=n)
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:go|move|jump)\s+to\s+(?:the\s+)?(?P<where>top|bottom|start|end|"
    r"end\s+of\s+line|start\s+of\s+line)$",
    name="caret_jump",
    examples=('go to the top',),
    # Above focus_app, which otherwise reads "go to the top" as a window named
    # "the top". The pattern only accepts caret positions, so it cannot steal a
    # real "go to <app>".
    priority=5,
    description="Move the text caret",
)
def do_caret_jump(ctx, where: str = "top", **_) -> ActionResult:
    where = where.replace(" ", "")
    mapping = {
        "top": ("ctrl", "home"),
        "start": ("ctrl", "home"),
        "bottom": ("ctrl", "end"),
        "end": ("ctrl", "end"),
        "endofline": ("end",),
        "startofline": ("home",),
    }
    combo = mapping[where]
    act.hotkey(*combo) if len(combo) > 1 else act.press(combo[0])
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:click|choose|select|tap|pick|hit)\s+(?:on\s+)?(?:the\s+)?(?P<label>.+?)"
    r"(?:\s+(?:button|option|icon|link|tab|profile|item))?"
    r"(?:\s+(?:in|on|from)\s+(?P<where>[\w .\-]+))?$",
    name="click_text",
    # Last resort. Everything specific -- "select all", "press enter",
    # "click at 400 300" -- must win first; this is what is left when the user
    # names something they can see rather than something we have a verb for.
    priority=-10,
    verbatim=True,
    description="Click something on screen by the words written on it",
    examples=("choose AD JAYANTAN", "click Sign in", "select Guest mode"),
)
def do_click_text(ctx, label: str = "", where: str | None = None, **_) -> ActionResult:
    from jarvis.core import screen

    label = (label or "").strip()
    if not label:
        return ActionResult.fail("Click what?")

    if where:
        # "choose AD JAYANTAN in chrome" -- bring that window forward first, so
        # the search looks at the right thing.
        from jarvis.skills.window import find_window, focus_window

        window = find_window(where)
        if window is not None:
            focus_window(window)

    target = screen.find(label)
    if target is None:
        # Neither the accessibility tree nor the words on screen could answer.
        # Before giving up, let a model look at the picture: "the third
        # option" and "the drive with the least space" are not written
        # anywhere and are not named controls, but they are plain to see.
        target = screen.find_by_vision(ctx, label)
    if target is None:
        return ActionResult.fail(
            f"I can't see {label} on screen.",
            "Nothing matching that is visible, or the app doesn't expose it.",
        )
    x, y = target.centre
    act.move(x, y)
    act.click()
    return ActionResult(
        ok=True,
        say="",
        detail=f"clicked {target.text!r} at {x},{y} via {target.source}",
        data={"matched": target.text, "x": x, "y": y, "source": target.source},
    )


# Spoken ordinals. "1st" and friends survive `normalize`, so both forms are
# listed rather than trying to parse digits and suffixes separately.
ORDINALS = {
    "first": 1, "1st": 1, "one": 1,
    "second": 2, "2nd": 2, "two": 2,
    "third": 3, "3rd": 3, "three": 3,
    "fourth": 4, "4th": 4, "four": 4,
    "fifth": 5, "5th": 5, "five": 5,
    "sixth": 6, "6th": 6, "six": 6,
    "seventh": 7, "7th": 7, "seven": 7,
    "eighth": 8, "8th": 8, "eight": 8,
    "ninth": 9, "9th": 9, "nine": 9,
    "tenth": 10, "10th": 10, "ten": 10,
    "last": -1,
}


@intent(
    r"^(?:click|choose|select|open|tap|pick|hit)\s+(?:on\s+)?(?:the\s+)?"
    r"(?P<which>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth"
    r"|last|1st|2nd|3rd|4th|5th|6th|7th|8th|9th|10th)"
    # "option", "one" and "thing" name no control type at all; they are here
    # because people say them, and the vision fallback is what answers them.
    r"\s+(?P<kind>link|result|button|item|tab|option|one|thing|icon|file"
    r"|folder|drive|row|card)s?$",
    name="click_nth",
    # Above click_text, which would otherwise take "first" as a label and go
    # looking for the word "first" written somewhere on screen. It is not, so
    # the honest answer was "I can't see first on screen" -- correct, and no
    # use to someone pointing at a list of search results.
    priority=4,
    description="Click something by its position on screen",
    examples=("click the first link", "open the second result", "click the last tab"),
)
def do_click_nth(ctx, which: str = "", kind: str = "", **_) -> ActionResult:
    from jarvis.core import screen

    index = ORDINALS.get((which or "").lower())
    if index is None:
        return ActionResult.fail(f"I don't know which one {which!r} is.")

    target = screen.find_nth(kind, index)
    if target is None:
        # The tree publishes nothing of that kind -- File Explorer's drive
        # tiles are not links, buttons or list items to it. Looking at the
        # picture is how "the third option" gets answered at all.
        target = screen.find_by_vision(ctx, f"the {which} {kind}")
    if target is None:
        return ActionResult.fail(
            f"I can't pick the {which} {kind} here.",
            "This app publishes no such controls, and looking at the screen "
            "did not settle it either. Naming what it says works instead.",
        )
    x, y = target.centre
    act.move(x, y)
    act.click()
    label = target.text or f"{which} {kind}"
    return ActionResult(
        ok=True,
        say="",
        detail=f"clicked the {which} {kind}, {label!r}, at {x},{y}",
        data={"matched": target.text, "x": x, "y": y, "index": index},
    )
