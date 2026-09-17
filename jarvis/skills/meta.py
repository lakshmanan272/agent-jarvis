"""Meta commands: talking to Jarvis about Jarvis."""
from __future__ import annotations

import random

from jarvis.core import actuator as act
from jarvis.skills.base import REGISTRY, ActionResult, intent

_GREETINGS = ("At your service.", "Ready.", "Listening.", "Go ahead.")


@intent(
    r"^(?:hi|hello|hey|yo|good\s+(?:morning|afternoon|evening))$",
    r"^are\s+you\s+(?:there|awake|ready)$",
    name="greet",
    examples=('hello',),
    priority=9,
    description="Say hello",
)
def do_greet(ctx, **_) -> ActionResult:
    return ActionResult(ok=True, say=random.choice(_GREETINGS))


@intent(
    r"^(?:stop|halt|abort|cancel|freeze|wait)$",
    r"^(?:stop|cancel)\s+(?:it|that|everything)$",
    name="stop",
    priority=100,
    description="Abort whatever is running",
)
def do_stop(ctx, **_) -> ActionResult:
    act.abort()
    ctx.pending_confirm = None
    act.clear_abort()
    return ActionResult(ok=True, say="Stopped.")


@intent(
    r"^(?:repeat|again|do\s+(?:it|that)\s+again|one\s+more\s+time)$",
    name="repeat",
    priority=20,
    description="Run the previous command again",
)
def do_repeat(ctx, **_) -> ActionResult:
    previous = ctx.variables.get("previous_command")
    if not previous:
        return ActionResult.fail("Nothing to repeat.")
    from jarvis.core.router import Router  # local import avoids a cycle

    router: Router | None = ctx.variables.get("router")
    if router is None:
        return ActionResult.fail("Nothing to repeat.")
    return router.dispatch(previous)


@intent(
    r"^(?:what\s+can\s+you\s+do|help|commands|show\s+commands)$",
    name="help",
    priority=9,
    description="List what Jarvis understands",
)
def do_help(ctx, **_) -> ActionResult:
    lines = []
    for item in sorted(REGISTRY, key=lambda i: i.name):
        example = item.examples[0] if item.examples else item.name.replace("_", " ")
        lines.append(f"{item.name:<18} {example}")
    return ActionResult(
        ok=True,
        say=f"I know {len(REGISTRY)} commands. The list is on screen.",
        detail="\n".join(lines),
        data={"count": len(REGISTRY)},
    )


@intent(
    r"^(?:go\s+to\s+sleep|sleep\s+mode|stop\s+listening|mute\s+yourself)$",
    name="sleep_listening",
    examples=('go to sleep',),
    priority=12,
    description="Stop acting on speech until woken by hotkey or wake word",
)
def do_sleep(ctx, **_) -> ActionResult:
    ctx.variables["muted"] = True
    return ActionResult(ok=True, say="Sleeping. Say Jarvis to wake me.")


@intent(
    r"^(?:wake\s+up|start\s+listening|unmute\s+yourself)$",
    name="wake_listening",
    examples=('wake up',),
    priority=12,
    description="Resume acting on speech",
)
def do_wake(ctx, **_) -> ActionResult:
    ctx.variables["muted"] = False
    return ActionResult(ok=True, say="Listening.")


@intent(
    r"^(?:speak|talk)\s+(?P<state>on|off)$",
    r"^(?:be\s+)?(?P<state>quiet|silent)$",
    name="toggle_voice",
    examples=('speak off',),
    priority=10,
    description="Turn spoken replies on or off",
)
def do_toggle_voice(ctx, state: str = "off", **_) -> ActionResult:
    enabled = state == "on"
    ctx.config.voice_out.enabled = enabled
    return ActionResult(ok=True, say="Voice on." if enabled else "")


@intent(
    r"^(?:shut\s*down|exit|quit|close)\s+(?:jarvis|yourself)$",
    # `normalize` strips the wake word, so "exit jarvis" arrives as "exit" and
    # the pattern above can never fire by voice. Bare "exit"/"quit" is free:
    # close_app requires something to close after the verb.
    r"^(?:exit|quit)$",
    r"^goodbye$",
    name="exit_jarvis",
    examples=('exit jarvis',),
    priority=30,
    description="Shut Jarvis down",
)
def do_exit(ctx, **_) -> ActionResult:
    ctx.variables["exit"] = True
    return ActionResult(ok=True, say="Goodbye.")


@intent(
    r"^(?:how\s+fast|latency|benchmark|how\s+long\s+did\s+that\s+take)$",
    name="latency",
    priority=9,
    description="Report how long the last command took",
)
def do_latency(ctx, **_) -> ActionResult:
    if ctx.last_result is None:
        return ActionResult(ok=True, say="Nothing run yet.")
    ms = ctx.last_result.data.get("elapsed_ms", 0)
    return ActionResult(ok=True, say=f"Last command took {ms} milliseconds.")
