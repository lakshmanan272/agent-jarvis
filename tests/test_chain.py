"""Compound commands: "open notepad and type hello" is two actions, not one.

Before this, the whole tail landed in the first intent's capture group, so
`open_app` was asked to launch an application called "notepad and type hello
lakshmanan welcome" — and the fuzzy window matcher cheerfully agreed it had
found it.
"""
from __future__ import annotations

import pytest

from jarvis.nlp.chain import split_commands


@pytest.mark.parametrize(
    "phrase,expected",
    [
        # the reported failure
        ("open notepad and type hello lakshmanan welcome",
         ["open notepad", "type hello lakshmanan welcome"]),
        # strong separators always split
        ("open chrome then go to youtube", ["open chrome", "go to youtube"]),
        ("minimize and then open spotify", ["minimize", "open spotify"]),
        ("click; type done", ["click", "type done"]),
        ("save after that close notepad", ["save", "close notepad"]),
        # three steps
        ("open notepad and type hi and press enter",
         ["open notepad", "type hi", "press enter"]),
    ],
)
def test_splits_where_it_should(phrase, expected):
    assert split_commands(phrase) == expected


@pytest.mark.parametrize(
    "phrase",
    [
        # "and" joining words, not commands — splitting these would corrupt
        # the payload, which is worse than not chaining at all.
        "search for cats and dogs",
        "type hello and welcome",
        "type fish and chips",
        "search for salt and pepper on youtube",
        "what time is it",
        "open chrome",
    ],
)
def test_leaves_single_commands_alone(phrase):
    assert split_commands(phrase) == [phrase]


def test_splits_only_at_the_verb():
    assert split_commands("type fish and chips and open chrome") == [
        "type fish and chips",
        "open chrome",
    ]


def test_empty_input():
    assert split_commands("") == []
    assert split_commands("   ") == []


def test_step_count_is_capped():
    phrase = " and ".join(["press tab"] * 30)
    assert len(split_commands(phrase)) <= 8


# --- running a chain -------------------------------------------------------


def test_chain_runs_every_step(router, no_real_input, side_effects):
    result = router.dispatch("select all and copy and press enter")
    assert result.ok
    assert result.data["intent"] == "chain"
    assert result.data["steps"] == ["select all", "copy", "press enter"]
    assert [c[0] for c in no_real_input] == ["hotkey", "hotkey", "press"]


def test_chain_types_the_verbatim_payload(router, no_real_input, monkeypatch):
    from jarvis.skills import apps, window

    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    monkeypatch.setattr(apps, "launch", lambda _t: True)
    monkeypatch.setattr(window, "wait_for_window", lambda *a, **k: object())

    router.dispatch("open notepad and type Hello Lakshmanan Welcome")
    typed = [c[1][0] for c in no_real_input if c[0] == "type_text"]
    assert typed == ["Hello Lakshmanan Welcome"]


def test_chain_waits_for_a_launched_window_before_the_next_step(
    router, no_real_input, monkeypatch
):
    """Typing must not start while the app is still opening."""
    from jarvis.skills import apps, window

    order: list[str] = []
    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    monkeypatch.setattr(apps, "launch", lambda _t: order.append("launch") or True)
    monkeypatch.setattr(
        window, "wait_for_window", lambda *a, **k: order.append("wait") or object()
    )
    monkeypatch.setattr(
        router.ctx.config.control, "paste_threshold", 999, raising=False
    )
    from jarvis.core import actuator

    monkeypatch.setattr(actuator, "type_text", lambda *a, **k: order.append("type"))

    router.dispatch("open notepad and type hi")
    assert order == ["launch", "wait", "type"]


def test_chain_stops_at_the_first_failure(router, no_real_input, monkeypatch):
    from jarvis.skills import apps

    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    monkeypatch.setattr(apps, "resolve_target", lambda _n: None)  # unknown app

    result = router.dispatch("open zzzznotanapp and select all")
    assert result.ok is False
    assert "Stopped at step 1" in result.say
    assert result.data["completed"] == []
    # nothing after the failure should have run
    assert no_real_input == []


def test_chain_reports_which_steps_completed(router, no_real_input, monkeypatch):
    from jarvis.skills import apps

    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    monkeypatch.setattr(apps, "resolve_target", lambda _n: None)

    result = router.dispatch("select all and open zzzznotanapp and copy")
    assert result.ok is False
    assert result.data["completed"] == ["select all"]
    assert "step 2" in result.say


def test_chain_stops_when_a_step_wants_confirmation(router, side_effects):
    result = router.dispatch("select all and shutdown the computer and copy")
    assert router.ctx.pending_confirm is not None
    assert "confirm" in result.say.lower()
    assert "more steps" in result.detail
    assert side_effects == []


def test_a_confirmation_answer_is_never_treated_as_a_chain(router, side_effects):
    router.dispatch("shutdown the computer")
    result = router.dispatch("yes")
    assert result.data["intent"] == "confirm"


def test_fuzzy_match_rejects_a_sentence_that_merely_contains_an_app_name():
    """The other half of the reported bug."""
    from jarvis.nlp.matcher import best_match

    windows = {"untitled - notepad": "Untitled - Notepad"}
    assert best_match("notepad and type hello lakshmanan welcome", windows) is None
    # ...while still matching the things it should
    assert best_match("notepad", windows) == "Untitled - Notepad"
    assert best_match("note pad", windows) == "Untitled - Notepad"


# --- "open chrome and youtube" stalled for six seconds ---------------------
# "youtube" is not a command verb so the chain left the phrase whole; the app
# resolver fuzzy-matched "chrome" out of it and then the chain waited for a
# window called "chrome and youtube" that was never going to exist.


def test_open_with_two_targets_opens_both(router, side_effects, monkeypatch):
    from jarvis.skills import apps

    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    opened = []
    monkeypatch.setattr(apps, "launch", lambda t: opened.append(t) or True)
    from jarvis.skills import web

    monkeypatch.setattr(web, "open_url", lambda u: opened.append(u))

    result = router.dispatch("open chrome and youtube")
    assert result.ok
    assert "chrome" in opened
    assert any("youtube" in str(o) for o in opened)


def test_a_launch_that_opens_no_window_does_not_stall(router, monkeypatch):
    """A protocol handler opens someone else's window; waiting is pure delay."""
    from jarvis.skills import apps

    monkeypatch.setattr(apps, "find_window", lambda _n: None)
    monkeypatch.setattr(apps, "resolve_target", lambda _n: "ms-settings:")
    monkeypatch.setattr(apps, "launch", lambda _t: True)

    waited = []
    from jarvis.skills import window

    monkeypatch.setattr(
        window, "wait_for_window", lambda *a, **k: waited.append(a) or None
    )
    result = router.dispatch("open settings and select all")
    assert result.ok
    assert waited == [], "a protocol launch has no window to wait for"


def test_the_window_wait_is_bounded():
    from jarvis.core.router import WINDOW_SETTLE_S

    assert WINDOW_SETTLE_S <= 3.0
