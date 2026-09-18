"""Acting on what the screen looks like, when nothing else knows how.

Every other skill knows in advance what it is doing. This one does not: it
takes a goal, looks at the screen, decides one action, performs it, and looks
again. That is the only way to work an application Jarvis has no command for.

It is also the least predictable thing here, and the only thing that sends a
picture of the user's screen to a model, so it is opt-in by name, confirmed
before it starts, bounded in steps, and forbidden from touching Jarvis's own
windows.
"""
from __future__ import annotations

import io
import json
import logging
import re
import time

from jarvis.core import actuator as act
from jarvis.core import focus
from jarvis.skills.base import ActionResult, intent

log = logging.getLogger("jarvis.autopilot")

# Each step costs a screenshot, a vision call and an action -- roughly two
# seconds. Eight is enough for "turn on dark mode" and short enough that a
# loop going nowhere is over before it becomes irritating.
MAX_STEPS = 8

# The model reads a scaled-down screen. Full resolution costs tokens and
# latency without improving where it says to click, because it answers in the
# image's own coordinates and those are scaled back up here.
VIEW_WIDTH = 1280

# How long to let an action settle before looking again: a click that opens a
# menu needs the menu to be on screen in the next screenshot.
SETTLE_S = 0.6

SYSTEM = """You operate a Windows desktop to reach a goal, one action at a time.

You see a screenshot {w} by {h} pixels. Give coordinates in that image's own
pixels; they are scaled to the real screen for you.

{forbidden}

Reply with ONE JSON object and nothing else:
{{"thought": "what you see and why this action", "action": "...", ...}}

action is one of:
  "click" | "double_click" | "right_click"   with BOTH "x" and "y", always
  "type"                                     with "text"
  "hotkey"                                   with "keys": ["ctrl", "t"]
  "press"                                    with "key": "enter"
  "scroll"                                   with "amount" (negative is down)
  "wait"                                     nothing else
  "done"                                     with "message": what was achieved
  "give_up"                                  with "message": why it cannot be done

Rules:
- One action. Do not plan ahead in the JSON.
- A click without both "x" and "y" is discarded and the step is wasted.
  Measured: one reply in three left "y" out. Include both every time.
- If the goal is already satisfied by what you see, answer "done" immediately.
- If you cannot see a way forward, answer "give_up". Do not guess coordinates
  for something that is not visible.
- No emojis anywhere."""


def _screenshot() -> tuple[bytes, int, int, float]:
    """A scaled PNG of the screen, with the factor that undoes the scaling."""
    import pyautogui

    shot = pyautogui.screenshot()
    full_width = shot.width
    if shot.width > VIEW_WIDTH:
        shot.thumbnail((VIEW_WIDTH, VIEW_WIDTH))
    buffer = io.BytesIO()
    shot.save(buffer, format="PNG")
    return buffer.getvalue(), shot.width, shot.height, full_width / shot.width


def _forbidden_note(scale: float) -> str:
    """Tell the model where Jarvis is, in the coordinates it will answer in."""
    rects = focus.our_window_rects()
    if not rects:
        return "Nothing on screen is off limits."
    zones = "; ".join(
        f"x {int(left / scale)}-{int(right / scale)}, "
        f"y {int(top / scale)}-{int(bottom / scale)}"
        for left, top, right, bottom in rects
    )
    return (
        "NEVER click inside these regions -- they are this assistant's own "
        f"windows, not part of the task: {zones}."
    )


def _inside_ours(x: int, y: int) -> bool:
    return any(
        left <= x <= right and top <= y <= bottom
        for left, top, right, bottom in focus.our_window_rects()
    )


def _decision(raw: str) -> dict | None:
    """The JSON object out of a reply that may be fenced or chatty."""
    if not raw:
        return None
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _perform(step: dict, scale: float, width: int, height: int) -> str | None:
    """Carry out one decided action. Returns a refusal, or None if it ran."""
    action = str(step.get("action", "")).lower()

    if action in ("click", "double_click", "right_click"):
        try:
            x, y = int(step["x"]), int(step["y"])
        except (KeyError, TypeError, ValueError):
            return "no coordinates given"
        if not (0 <= x <= width and 0 <= y <= height):
            return f"coordinates {x},{y} are outside the screen"
        real_x, real_y = int(x * scale), int(y * scale)
        if _inside_ours(real_x, real_y):
            # The model was told not to. Refusing rather than clamping means
            # the refusal goes back to it and it can choose somewhere else.
            return "that is inside Jarvis's own window"
        button = "right" if action == "right_click" else "left"
        act.click(
            button=button,
            clicks=2 if action == "double_click" else 1,
            x=real_x,
            y=real_y,
        )
        return None

    if action == "type":
        text = step.get("text") or ""
        if not text:
            return "nothing to type"
        act.type_text(str(text))
        return None

    if action == "hotkey":
        keys = step.get("keys") or []
        if not keys:
            return "no keys given"
        act.hotkey(*[str(k) for k in keys])
        return None

    if action == "press":
        key = step.get("key")
        if not key:
            return "no key given"
        act.press(str(key))
        return None

    if action == "scroll":
        act.scroll(int(step.get("amount", -3)))
        return None

    if action == "wait":
        time.sleep(1.0)
        return None

    return f"unknown action {action!r}"


@intent(
    r"^(?:work\s+out|figure\s+out|take\s+over\s+and)\s+(?P<goal>.+)$",
    r"^autopilot\s+(?P<goal>.+)$",
    name="autopilot",
    priority=9,
    verbatim=True,
    destructive=True,
    examples=("work out how to turn on dark mode",),
    description="Look at the screen and act, step by step, to reach a goal",
)
def do_autopilot(ctx, goal: str = "", _confirmed: bool = False, **_) -> ActionResult:
    goal = (goal or "").strip()
    if not goal:
        return ActionResult.fail("Work out what?")

    # This drives the mouse and keyboard on its own judgement and sends a
    # picture of the screen to a model, so it asks first even where other
    # destructive commands would not.
    if not _confirmed and ctx.config.control.confirm_destructive:
        return ActionResult(
            ok=True,
            needs_confirm=(
                f"That means sending pictures of your screen to the model and "
                f"letting me drive, to {goal}. Say yes or no."
            ),
        )

    from jarvis.nlp.brain import Brain

    brain = Brain(ctx.config.brain)
    if not brain.available:
        return ActionResult.fail(
            "I need a model with vision for that.", "brain disabled or unconfigured"
        )

    history: list[str] = []
    for step_number in range(1, MAX_STEPS + 1):
        # Raises InterruptedError if the user pressed End. That travels up
        # through the router and abandons the rest of any chain.
        act._check()

        png, width, height, scale = _screenshot()
        system = SYSTEM.format(w=width, h=height, forbidden=_forbidden_note(scale))
        question = (
            f"Goal: {goal}\n"
            f"Step {step_number} of {MAX_STEPS}.\n"
            + (f"Already done: {'; '.join(history[-3:])}\n" if history else "")
            + "What is the single next action?"
        )
        reply = brain.look(system, question, png)
        step = _decision(reply or "")
        if step is None:
            log.warning("unreadable decision: %r", (reply or "")[:200])
            return ActionResult.fail(
                "I couldn't work that out.",
                f"unreadable reply at step {step_number}",
            )

        thought = str(step.get("thought", ""))[:200]
        action = str(step.get("action", "")).lower()
        log.info("autopilot %d/%d %s: %s", step_number, MAX_STEPS, action, thought)

        if action == "done":
            return ActionResult(
                ok=True,
                say=str(step.get("message") or "Done."),
                detail=" -> ".join(history) or thought,
                data={"steps": step_number, "history": history},
            )
        if action == "give_up":
            return ActionResult.fail(
                str(step.get("message") or "I can't get there from here."),
                " -> ".join(history),
            )

        refusal = _perform(step, scale, width, height)
        if refusal is not None:
            # Recorded, not fatal: the next screenshot plus this note is
            # usually enough for the model to choose differently.
            log.info("autopilot refused step: %s", refusal)
            history.append(f"{action} refused ({refusal})")
        else:
            history.append(f"{action}: {thought[:60]}")
            time.sleep(SETTLE_S)

    return ActionResult.fail(
        f"I got part of the way but ran out of steps after {MAX_STEPS}.",
        " -> ".join(history),
    )
