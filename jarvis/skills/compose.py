"""Writing prose, and reading the screen back.

Everything else in `skills/` carries out an instruction whose content is already
known — which key, which app, which coordinates. These two are different: the
answer does not exist until a model produces it, so both are unavailable
without `brain.enabled` and both say so plainly rather than failing obscurely.
"""
from __future__ import annotations

import logging

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

log = logging.getLogger("jarvis.skills.compose")

# Roughly how much to write when the user does not say. Long enough to be worth
# asking for, short enough that a mistake is quick to undo.
DEFAULT_WORDS = 180

_LENGTH_WORDS = {
    "short": 80, "brief": 80, "quick": 80,
    "long": 400, "detailed": 400, "full": 400,
    "paragraph": 120, "essay": 400, "note": 90, "summary": 100,
}


def _brain(ctx):
    return ctx.variables.get("router").brain if ctx.variables.get("router") else None


@intent(
    r"^(?:write|compose|draft)\s+(?:me\s+)?(?:an?\s+)?"
    r"(?P<length>short|brief|quick|long|detailed|full|paragraph|essay|note|summary)?"
    r"\s*(?:paragraph|essay|note|summary|article|piece)?\s*"
    r"(?:about|on|regarding)\s+(?P<topic>.+)$",
    name="compose_text",
    verbatim=True,
    priority=7,
    description="Write about something and type it where the cursor is",
    examples=("write about actor vijay", "write a short note about python"),
)
def do_compose(ctx, topic: str = "", length: str | None = None, **_) -> ActionResult:
    topic = (topic or "").strip()
    if not topic:
        return ActionResult.fail("Write about what?")

    brain = _brain(ctx)
    if brain is None or not brain.available:
        return ActionResult.fail(
            "I can't write without a model.",
            "Set brain.enabled in ~/.jarvis/config.json.",
        )

    words = _LENGTH_WORDS.get((length or "").strip().lower(), DEFAULT_WORDS)
    text = brain.compose(f"Write about {topic}", words=words)
    if not text:
        return ActionResult.fail(f"I couldn't write about {topic}.")

    cfg = ctx.config.control
    act.type_text(text, paste_threshold=cfg.paste_threshold, interval=cfg.type_interval_s)
    return ActionResult(
        ok=True,
        say=f"Written, about {len(text.split())} words.",
        detail=text[:200],
        data={"words": len(text.split()), "chars": len(text)},
    )


@intent(
    r"^(?:what(?:'s|s| is)?\s+(?:on\s+)?(?:the\s+)?screen|what\s+do\s+you\s+see|"
    r"describe\s+(?:the\s+)?screen|read\s+(?:the\s+)?screen|"
    r"what(?:'s|s| is)?\s+(?:in\s+)?front\s+of\s+me)$",
    name="describe_screen",
    priority=8,
    description="Say what is currently visible on screen",
    examples=("what's on screen", "what do you see"),
)
def do_describe_screen(ctx, **_) -> ActionResult:
    """Read the screen with OCR and summarise what is there.

    The OCR pass returns a few hundred loose words in reading order, which is
    not an answer to "what am I looking at". A model turns that into a sentence;
    without one, the most prominent lines are reported raw, which is less useful
    but honest about what was actually seen.
    """
    from jarvis.core import screen

    words = screen._ocr_words()
    if not words:
        return ActionResult.fail(
            "I can't read the screen.",
            "Windows OCR is unavailable, or the screen is empty.",
        )

    # Lines carry more meaning than words; `_ocr_words` appends each whole line
    # after its words, and those are the longer entries.
    lines = [text for text, _box in words if " " in text]
    seen: set[str] = set()
    unique = [ln for ln in lines if not (ln in seen or seen.add(ln))]
    visible = "\n".join(unique[:60])

    brain = _brain(ctx)
    if brain is None or not brain.available:
        head = "; ".join(unique[:4])
        return ActionResult(
            ok=True,
            say=f"I can see: {head}." if head else "Nothing readable.",
            detail=visible,
        )

    summary = brain.compose(
        "Here is the text currently visible on a computer screen, read by OCR "
        "and possibly jumbled. In two or three sentences, say what the user is "
        "looking at -- which application and what is in it. Do not list the "
        f"text back.\n\n{visible}",
        words=60,
    )
    if not summary:
        head = "; ".join(unique[:4])
        return ActionResult(ok=True, say=f"I can see: {head}.", detail=visible)
    return ActionResult(ok=True, say=summary, detail=visible)


# --- dictation -------------------------------------------------------------
# A mode rather than a command: while it is on, speech is typed instead of
# being interpreted. The engine checks `ctx.variables["dictating"]` before it
# routes anything, so nothing here has to intercept the pipeline.


@intent(
    r"^(?:start\s+)?dictation(?:\s+mode)?$",
    r"^(?:start\s+dictating|take\s+dictation|type\s+what\s+i\s+say)$",
    name="start_dictation",
    priority=12,
    description="Type everything said, until told to stop",
    examples=("start dictation", "type what i say"),
)
def do_start_dictation(ctx, **_) -> ActionResult:
    ctx.variables["dictating"] = True
    ctx.variables["dictating_private"] = False
    return ActionResult(
        ok=True,
        say="Dictating. Say stop dictation when you're done.",
        data={"dictating": True},
    )


@intent(
    r"^(?:start\s+)?(?:private|secret|password)\s+dictation$",
    r"^dictate\s+(?:a\s+)?(?:password|secret)$",
    name="start_private_dictation",
    priority=13,
    description="Dictate without the words being shown or logged",
    examples=("private dictation", "dictate a password"),
)
def do_start_private_dictation(ctx, **_) -> ActionResult:
    """Dictation for things that must not be written down anywhere.

    A spoken password would otherwise pass through two places it has no
    business being: the HUD, which displays what was heard, and jarvis.log,
    which records every dispatched command. In this mode the text is typed and
    nothing else -- the bar shows dots and the log records a length.
    """
    ctx.variables["dictating"] = True
    ctx.variables["dictating_private"] = True
    return ActionResult(
        ok=True,
        say="Private dictation. Nothing will be shown or logged.",
        data={"dictating": True, "private": True},
    )


@intent(
    r"^(?:stop|end|finish|exit)\s+dictation$",
    r"^stop\s+dictating$",
    name="stop_dictation",
    priority=13,
    description="Stop typing what is said",
    examples=("stop dictation",),
)
def do_stop_dictation(ctx, **_) -> ActionResult:
    was = ctx.variables.get("dictating")
    ctx.variables["dictating"] = False
    ctx.variables["dictating_private"] = False
    return ActionResult(
        ok=True,
        say="Dictation off." if was else "Wasn't dictating.",
        data={"dictating": False},
    )
