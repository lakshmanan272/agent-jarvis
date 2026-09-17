"""Drive the real router with the hardest phrasings and report what it does.

Everything here is genuine: real normalisation, real chaining, real verbatim
extraction, real intent matching, real handlers. Only the final mouse/keyboard
primitives and the process/browser launches are recorded instead of performed,
so this can run at full speed without touching the desktop.

    python tools/stress_test.py

Each case declares what the agent is expected to end up *doing*, not merely
which intent it picked — a command that routes correctly and then acts on the
wrong text is still broken.
"""
from __future__ import annotations

import contextlib
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import logging_setup  # noqa: E402
from jarvis.config import Config  # noqa: E402
from jarvis.core import actuator  # noqa: E402
from jarvis.core.router import Router  # noqa: E402
from jarvis.skills import apps, web, window  # noqa: E402
from jarvis.skills.base import Context  # noqa: E402

ACTIONS: list[tuple[str, tuple, dict]] = []


def _install_recorders() -> None:
    """Replace every real side effect with a recording stub."""
    for name in (
        "click", "move", "move_relative", "drag_to", "scroll", "press",
        "hotkey", "type_text", "mouse_down", "mouse_up", "write_clipboard",
    ):
        setattr(
            actuator, name,
            lambda *a, _n=name, **k: ACTIONS.append((_n, a, k)),
        )
    actuator.screen_size = lambda: (1920, 1080)
    actuator.position = lambda: (960, 540)
    actuator.read_clipboard = lambda: "clipboard contents"

    apps.launch = lambda t: ACTIONS.append(("launch", (t,), {})) or True
    apps.find_window = lambda _n: None
    window.wait_for_window = lambda *a, **k: object()
    web.open_url = lambda u: ACTIONS.append(("open_url", (u,), {}))
    # "play X" would otherwise hit the network for a video id.
    web.first_youtube_video = lambda q, timeout=4.0: "STUBVIDEO01"


@dataclass
class Case:
    """One phrase and what the agent must end up doing because of it."""

    phrase: str
    note: str
    typed: list[str] = field(default_factory=list)   # exact text typed, in order
    keys: list[tuple] = field(default_factory=list)  # exact key chords, in order
    launched: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)    # substrings that must appear
    intent: str | None = None
    ok: bool | None = True
    needs_confirm: bool = False
    no_actions: bool = False


CASES: list[Case] = [
    # --- long dependent chains ---------------------------------------------
    Case(
        "jarvis open notepad and type Meeting Notes and press enter and save",
        "four dependent steps, mixed verbs",
        typed=["Meeting Notes"],
        keys=[("enter",), ("ctrl", "s")],
        launched=["notepad"],
        intent="chain",
    ),
    Case(
        "open notepad then type Hello and then select all and copy",
        "strong and weak separators in one breath",
        typed=["Hello"],
        keys=[("ctrl", "a"), ("ctrl", "c")],
        launched=["notepad"],
        intent="chain",
    ),
    # --- the payload must survive verbatim ---------------------------------
    Case(
        "type The Quick Brown Fox Jumps Over The Lazy Dog",
        "capitalisation preserved",
        typed=["The Quick Brown Fox Jumps Over The Lazy Dog"],
        intent="type_text",
    ),
    Case(
        "type 500 dollars & 20% off! call me at 5pm.",
        "symbols, digits and punctuation preserved",
        typed=["500 dollars & 20% off! call me at 5pm."],
        intent="type_text",
    ),
    Case(
        "type five hundred and twenty three",
        "spelled-out numbers must NOT become digits inside dictated text",
        typed=["five hundred and twenty three"],
        intent="type_text",
    ),
    Case(
        "type please tell jarvis i said just now okay",
        "filler words and the wake word are content here, not noise",
        typed=["please tell jarvis i said just now okay"],
        intent="type_text",
    ),
    Case(
        "type காலை வணக்கம்",
        "Tamil, which the old keystroke path could not type at all",
        typed=["காலை வணக்கம்"],
        intent="type_text",
    ),
    # --- "and" that must NOT split -----------------------------------------
    Case(
        "search for salt and pepper on youtube",
        "'and' joins words; engine named at the end",
        urls=["youtube.com", "salt+and+pepper"],
        intent="web_search",
    ),
    Case(
        "type fish and chips and open chrome",
        "splits at the verb only: the food stays together",
        typed=["fish and chips"],
        launched=["chrome"],
        intent="chain",
    ),
    # --- numbers that must be interpreted ----------------------------------
    Case(
        "press tab three times",
        "spelled number becomes a repeat count",
        keys=[("tab",), ("tab",), ("tab",)],
        intent="press_key",
    ),
    Case(
        "scroll down five",
        "spelled number becomes wheel notches",
        intent="scroll",
    ),
    Case(
        "select 3 words",
        "repeat a chord an exact number of times",
        keys=[("ctrl", "shift", "right")] * 3,
        intent="select_count",
    ),
    # --- politeness and wake-word stacking ---------------------------------
    Case(
        "hey jarvis could you please just open chrome for me now",
        "wake word plus four filler phrases",
        launched=["chrome"],
        intent="open_app",
    ),
    # --- multi-target open --------------------------------------------------
    Case(
        "open chrome and youtube",
        "an app and a bookmarked site in one command",
        launched=["chrome"],
        urls=["youtube.com"],
        intent="open_app",
    ),
    # --- play actually plays ------------------------------------------------
    Case(
        "play theme song",
        "resolves a video rather than opening a search page",
        urls=["watch?v=STUBVIDEO01"],
        intent="play_youtube",
    ),
    # --- destructive work stops for confirmation ---------------------------
    Case(
        "select all and shutdown the computer and copy",
        "a chain halts at the step that needs a yes",
        keys=[("ctrl", "a")],
        intent="chain",
        needs_confirm=True,
    ),
    # --- things it should refuse cleanly ------------------------------------
    Case(
        "make me a sandwich",
        "unknown request fails without acting",
        ok=False,
        no_actions=True,
    ),
    Case(
        "the in their eyes much learn a new and up at",
        "song lyrics from the speakers are not a command",
        ok=False,
        no_actions=True,
    ),
    Case(
        "click at 9000 9000",
        "off-screen coordinates are refused",
        ok=False,
        no_actions=True,
    ),
    # --- exactness under pressure -------------------------------------------
    Case(
        "replace cat with dog",
        "two verbatim payloads in one command",
        intent="find_replace",
    ),
    Case(
        "find pricing details on this page",
        "search within the page, not the web",
        typed=["pricing details"],
        keys=[("ctrl", "f"), ("enter",)],
        intent="find_in_page",
    ),
]


def run() -> int:
    # Windows consoles default to cp1252, which cannot print the Tamil case.
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    logging_setup.setup("CRITICAL", console=False)
    _install_recorders()
    ctx = Context(config=Config(), speak=lambda _t: None)
    router = Router(ctx)
    ctx.variables["router"] = router

    passed = failed = 0
    slowest = 0.0
    print(f"{'':2} {'phrase':58} {'intent':12} {'ms':>7}  note")
    print("-" * 110)

    for case in CASES:
        ACTIONS.clear()
        ctx.pending_confirm = None
        start = time.perf_counter()
        result = router.dispatch(case.phrase)
        elapsed = (time.perf_counter() - start) * 1000
        slowest = max(slowest, elapsed)

        problems = _check(case, result, ACTIONS, ctx)
        mark = "OK" if not problems else "!!"
        if problems:
            failed += 1
        else:
            passed += 1
        phrase = case.phrase if len(case.phrase) <= 58 else case.phrase[:55] + "..."
        print(
            f"{mark:2} {phrase:58} {str(result.data.get('intent')):12} "
            f"{elapsed:7.1f}  {case.note}"
        )
        for problem in problems:
            print(f"{'':2} {'':58} {'':12} {'':7}  -> {problem}")

    print("-" * 110)
    print(f"{passed} passed, {failed} failed, slowest {slowest:.1f} ms")
    return 1 if failed else 0


def _check(case: Case, result, actions, ctx) -> list[str]:
    problems: list[str] = []

    if case.ok is not None and result.ok != case.ok:
        problems.append(f"ok={result.ok}, expected {case.ok} (say={result.say!r})")
    if case.intent and result.data.get("intent") != case.intent:
        problems.append(f"intent={result.data.get('intent')!r}, expected {case.intent!r}")

    typed = [a[1][0] for a in actions if a[0] == "type_text"]
    if case.typed and typed != case.typed:
        problems.append(f"typed {typed!r}, expected {case.typed!r}")

    if case.keys:
        # press(key, presses=3) is three presses, not one.
        chords: list[tuple] = []
        for name, args, kwargs in actions:
            if name not in ("hotkey", "press"):
                continue
            chords.extend([args] * kwargs.get("presses", 1))
        if chords != case.keys:
            problems.append(f"keys {chords!r}, expected {case.keys!r}")

    launched = [a[1][0] for a in actions if a[0] == "launch"]
    for expected in case.launched:
        if not any(expected in got for got in launched):
            problems.append(f"never launched {expected!r} (launched {launched!r})")

    urls = [a[1][0] for a in actions if a[0] == "open_url"]
    for fragment in case.urls:
        if not any(fragment in url for url in urls):
            problems.append(f"no url containing {fragment!r} (opened {urls!r})")

    if case.needs_confirm and ctx.pending_confirm is None:
        problems.append("expected a pending confirmation, got none")
    if not case.needs_confirm and ctx.pending_confirm is not None:
        problems.append("left a confirmation pending unexpectedly")

    if case.no_actions and actions:
        problems.append(f"expected no side effects, got {[a[0] for a in actions]}")

    return problems


if __name__ == "__main__":
    raise SystemExit(run())
