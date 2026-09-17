"""Regressions for bugs found by driving the real app.

Each test here corresponds to something that actually misbehaved on a live
desktop, not a hypothetical. The comment above each one says what went wrong.
"""
from __future__ import annotations

import pytest


def typed(calls) -> str:
    hits = [c for c in calls if c[0] == "type_text"]
    return hits[0][1][0] if hits else ""


# --- dictated text was being mangled by the command normaliser -------------
# `normalize` lowercases, folds "five" to "5", and deletes filler words and the
# word "jarvis" anywhere in the phrase. Correct for recognising a command, and
# destructive for the text that command is supposed to type.


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("type Hello World", "Hello World"),
        ("type Just Do It", "Just Do It"),
        ("type please call me back", "please call me back"),
        ("type five hundred dollars", "five hundred dollars"),
        ("type tell jarvis i said hi", "tell jarvis i said hi"),
        ("jarvis please type Meeting Notes", "Meeting Notes"),
        ("type Don't forget: 3pm, room 2!", "Don't forget: 3pm, room 2!"),
    ],
)
def test_dictated_text_is_typed_verbatim(router, no_real_input, phrase, expected):
    router.dispatch(phrase)
    assert typed(no_real_input) == expected


def test_search_query_keeps_its_capitalisation(router, side_effects):
    router.dispatch("search for OpenAI and Anthropic")
    urls = [c[1][0] for c in side_effects if c[0] == "open_url"]
    assert urls and "OpenAI+and+Anthropic" in urls[0]


def test_non_verbatim_intents_still_get_normalised_groups(router, no_real_input):
    # "press tab three times" relies on the normaliser folding three -> 3.
    router.dispatch("press tab three times")
    presses = [c for c in no_real_input if c[0] == "press"]
    assert presses and presses[0][2]["presses"] == 3


# --- handlers saw the *previous* command, not the current one --------------
# ctx.last_command was assigned after the handler ran, so every handler that
# inspected it branched on stale state: "previous track" skipped forward.


def test_previous_track_goes_back(router, no_real_input):
    router.dispatch("next track")
    no_real_input.clear()
    router.dispatch("previous track")
    assert no_real_input[0][1][0] == "prevtrack"


def test_next_track_goes_forward(router, no_real_input):
    router.dispatch("previous track")
    no_real_input.clear()
    router.dispatch("next track")
    assert no_real_input[0][1][0] == "nexttrack"


def test_previous_tab_goes_back(router, no_real_input):
    router.dispatch("next tab")
    no_real_input.clear()
    router.dispatch("previous tab")
    assert no_real_input[0][1] == ("ctrl", "shift", "tab")


# --- "okay" was being erased before it could confirm anything --------------
# The confirmation check ran on normalised text, where "okay" is filler, so the
# answer became the empty string and the pending action was silently dropped.


@pytest.mark.parametrize("answer", ["yes", "yeah", "ok", "okay", "OK", "sure", "do it"])
def test_affirmations_confirm(router, answer, side_effects):
    router.dispatch("shutdown the computer")
    assert side_effects == [], "asking for confirmation must not act yet"
    result = router.dispatch(answer)
    assert result.data["intent"] == "confirm"
    assert result.say == "Shutdown now."
    # The command is only ever recorded, never run -- see tests/conftest.py.
    assert [c[1][0] for c in side_effects] == [["shutdown", "/s", "/t", "0"]]


@pytest.mark.parametrize("answer", ["no", "nope", "cancel", "never mind"])
def test_refusals_cancel(router, answer, side_effects):
    router.dispatch("shutdown the computer")
    result = router.dispatch(answer)
    assert result.say == "Cancelled."
    assert router.ctx.pending_confirm is None
    assert side_effects == []


def test_unrelated_answer_routes_normally_and_clears_the_prompt(router):
    router.dispatch("shutdown the computer")
    result = router.dispatch("what time is it")
    assert result.data["intent"] == "time"
    assert router.ctx.pending_confirm is None


def test_confirmation_replays_verbatim_arguments(router, tmp_path):
    victim = tmp_path / "Report Final.txt"
    victim.write_text("x", encoding="utf-8")
    router.dispatch(f"delete file {victim}")
    assert router.ctx.pending_confirm is not None
    router.dispatch("yes")
    assert not victim.exists()


# --- the LLM fallback could recurse ----------------------------------------
# A rewrite that also failed to match went straight back through the brain.


def test_brain_is_consulted_at_most_once(router):
    class LoopingBrain:
        available = True

        def __init__(self):
            self.calls = 0

        def plan(self, text, intents):
            self.calls += 1
            return "still not a real command"

    router.brain = LoopingBrain()
    result = router.dispatch("do something undefined")
    assert result.ok is False
    assert router.brain.calls == 1


# --- misc ------------------------------------------------------------------


def test_every_instant_command_resolves(router):
    """Partial dispatch must not fire phrases that match no intent at all."""
    from jarvis.core.engine import INSTANT_COMMANDS
    from jarvis.nlp.matcher import normalize

    confirmations = {"yes", "no"}
    unmatched = []
    for phrase in INSTANT_COMMANDS:
        if phrase in confirmations:
            continue
        text = normalize(phrase)
        if not any(i.match(text) for i in router.intents):
            unmatched.append(phrase)
    assert unmatched == []


def test_empty_input_is_reported_with_timing(router):
    result = router.dispatch("   ")
    assert result.ok is False
    assert "elapsed_ms" in result.data


def test_files_default_outside_the_install_directory(tmp_path, monkeypatch):
    """"create folder x" used to land in the app's own working directory."""
    from jarvis.skills import files

    monkeypatch.chdir(tmp_path)
    assert files.default_dir() != tmp_path
