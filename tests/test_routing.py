"""Routing tests: does a phrase reach the intent a user would expect?

Nothing here touches the real machine: tests/conftest.py stubs out the mouse,
the keyboard, and every process/browser/shutdown call, autouse for the suite.
"""
from __future__ import annotations

import pytest


def route(router, phrase: str) -> str:
    """Resolve a phrase to an intent name without running the handler."""
    from jarvis.nlp.matcher import normalize

    text = normalize(phrase)
    for intent in router.intents:
        if intent.match(text):
            return intent.name
    return "-"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        # mouse
        ("click", "click"),
        ("jarvis click", "click"),
        ("double click", "click"),
        ("right click", "click"),
        ("click at 400 300", "click_at"),
        ("move mouse right 200", "move_mouse"),
        ("drag to 100 100", "drag"),
        # keyboard and text
        ("type hello world", "type_text"),
        ("press enter", "press_key"),
        ("press ctrl s", "press_key"),
        ("press tab 3 times", "press_key"),
        ("select all", "select_all"),
        ("select 3 words", "select_count"),
        ("select the line", "select_unit"),
        ("copy", "copy"),
        ("paste that", "paste"),
        ("undo", "undo"),
        ("delete 5 words", "delete"),
        ("uppercase", "change_case"),
        ("replace cat with dog", "find_replace"),
        ("new line", "newline"),
        ("question mark", "insert_symbol"),
        # scrolling
        ("scroll down", "scroll"),
        ("scroll up 5", "scroll"),
        ("scroll to bottom", "scroll"),
        # apps and windows
        ("open chrome", "open_app"),
        ("launch notepad", "open_app"),
        ("switch to vscode", "focus_app"),
        ("close notepad", "close_app"),
        ("minimize", "minimize"),
        ("maximize", "maximize"),
        ("show desktop", "show_desktop"),
        ("snap to the left", "snap_window"),
        # web
        ("search for python decorators", "web_search"),
        ("search cats on youtube", "web_search"),
        ("play lofi beats", "play_youtube"),
        ("open youtube", "open_site"),
        ("go to github.com", "open_url"),
        ("new tab", "new_tab"),
        ("refresh", "refresh"),
        ("find pricing on page", "find_in_page"),
        # system
        ("volume 40", "set_volume"),
        ("set volume to 100", "set_volume"),
        ("volume up", "volume_step"),
        ("mute", "mute"),
        ("take a screenshot", "screenshot"),
        ("lock the screen", "lock"),
        ("what time is it", "time"),
        ("battery", "battery"),
        ("system status", "system_status"),
        # files
        ("create folder reports on desktop", "create_folder"),
        ("open downloads", "open_folder"),
        ("find files budget", "search_files"),
        # meta
        ("hello", "greet"),
        ("stop", "stop"),
        ("help", "help"),
        ("goodbye", "exit_jarvis"),
    ],
)
def test_phrase_routes_to_intent(router, phrase, expected):
    assert route(router, phrase) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        "please open chrome",
        "jarvis could you please open chrome",
        "hey jarvis open chrome now",
        "can you open chrome for me",
    ],
)
def test_politeness_is_stripped(router, phrase):
    assert route(router, phrase) == "open_app"


def test_unknown_phrase_fails_cleanly(router):
    result = router.dispatch("make me a sandwich")
    assert result.ok is False
    assert "don't know" in result.say


def test_handler_arguments_are_parsed(router, no_real_input):
    router.dispatch("type hello world")
    typed = [c for c in no_real_input if c[0] == "type_text"]
    assert typed and typed[0][1][0] == "hello world"


def test_scroll_amount_is_converted_to_wheel_units(router, no_real_input):
    router.dispatch("scroll down 5")
    scrolls = [c for c in no_real_input if c[0] == "scroll"]
    assert scrolls and scrolls[0][1][0] == -600  # 5 notches * 120, negative = down


def test_chord_presses_use_hotkey(router, no_real_input):
    router.dispatch("press ctrl shift s")
    assert [c for c in no_real_input if c[0] == "hotkey"]


def test_repeat_count_is_capped(router, no_real_input):
    router.dispatch("press tab 9999 times")
    presses = [c for c in no_real_input if c[0] == "press"]
    assert presses and presses[0][2]["presses"] == 50


def test_offscreen_coordinates_are_rejected(router):
    result = router.dispatch("click at 9000 9000")
    assert result.ok is False


def test_timing_is_recorded(router):
    result = router.dispatch("click")
    assert result.data["elapsed_ms"] >= 0
    assert result.data["intent"] == "click"


def test_destructive_command_asks_first(router):
    result = router.dispatch("shutdown the computer")
    assert router.ctx.pending_confirm is not None
    assert "confirm" in result.say.lower()


def test_declining_a_confirmation_cancels_it(router):
    router.dispatch("shutdown the computer")
    result = router.dispatch("no")
    assert result.ok and result.say == "Cancelled."
    assert router.ctx.pending_confirm is None


def test_handler_exception_becomes_a_failed_result(router, monkeypatch):
    from jarvis.skills import input_control

    def boom(*_a, **_k):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(input_control.act, "hotkey", boom)
    result = router.dispatch("select all")
    assert result.ok is False
    assert "simulated failure" in result.detail


# --- the catalog handed to the LLM planner must not be fiction --------------
# `Brain` shows the model one example phrasing per intent and asks it to reply
# with one of them. 33 of 91 entries were the intent's own name with the
# underscores removed -- "volume step", "power", "media track" -- none of which
# route anywhere. The planner dutifully echoed them back and every rewrite
# failed. Three of the entries also turned out to be genuinely unreachable
# commands: "exit jarvis" normalises to "exit", which its pattern could not
# match, so that command could never be given by voice at all.


def test_every_example_routes_to_its_own_intent(router):
    from jarvis.nlp.matcher import normalize

    wrong = []
    for intent in router.intents:
        examples = intent.examples or (intent.name.replace("_", " "),)
        for example in examples:
            text = normalize(example)
            hit = next((i.name for i in router.intents if i.match(text)), None)
            if hit != intent.name:
                wrong.append(f"{intent.name}: {example!r} routes to {hit!r}")
    assert wrong == [], "examples that do not do what they claim:\n" + "\n".join(wrong)


def test_the_catalog_only_offers_working_phrasings(router):
    """End to end: every line the planner is actually shown must be sayable.

    Intents without an explicit example fall back to their own name, which for
    "stop", "copy" or "undo" is exactly what a user would say and for
    "volume_step" or "media_track" is not. This checks the result rather than
    demanding ceremony from the former.
    """
    from jarvis.config import Config
    from jarvis.nlp.brain import Brain
    from jarvis.nlp.matcher import normalize

    catalog = Brain(Config().brain)._build_catalog(router.intents)
    offered = [
        line.split("  (")[0].removeprefix("- ").strip()
        for line in catalog.splitlines()
        if line.startswith("- ")
    ]
    assert offered, "catalog is empty"
    dead = [
        phrase
        for phrase in offered
        if not any(i.match(normalize(phrase)) for i in router.intents)
    ]
    assert dead == [], f"catalog offers phrases that match nothing: {dead}"
