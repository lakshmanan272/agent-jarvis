"""Finding a thing on screen by the words printed on it.

"Choose AD JAYANTAN" has no coordinate to aim at: the profile card is wherever
Chrome drew it. The accessibility tree answers for native apps; Chrome's own
profile picker publishes nine elements and none of them are the profiles, so
OCR answers for the rest.
"""
from __future__ import annotations

import pytest

from jarvis.core import screen
from jarvis.core.screen import MATCH_FLOOR, Target, _excluded, _score

# --- scoring ---------------------------------------------------------------
# A wrong click lands somewhere arbitrary on the desktop, so the bar for
# "this is the thing" is deliberately high.


@pytest.mark.parametrize(
    "query,candidate",
    [
        ("ad jayantan", "AD JAYANTAN"),
        ("ad jayantan", "AD JAYANTAN Profile 3"),   # extra words on the label
        ("guest mode", "Guest mode"),
        ("sign in", "Sign in to Chrome"),
        ("edit", "Edit"),
        ("save", "Save as..."),
    ],
)
def test_real_labels_are_accepted(query, candidate):
    assert _score(query, candidate) >= MATCH_FLOOR


@pytest.mark.parametrize(
    "query,candidate",
    [
        # A stray letter used to score 100: partial_ratio treats any single
        # character of the query as a perfect partial match, and searching for
        # "ZEPHYRQUOKKA" matched a "Q" in a toolbar.
        ("ZEPHYRQUOKKA", "Q"),
        ("ad jayantan", "A"),
        ("edit", "E"),
        ("save", "S"),
        # ...and an unrelated label is not a near miss either.
        ("ad jayantan", "Krishna"),
        ("guest mode", "Downloads"),
    ],
)
def test_wrong_labels_are_refused(query, candidate):
    assert _score(query, candidate) < MATCH_FLOOR


def test_empty_inputs_score_nothing():
    assert _score("", "anything") == 0.0
    assert _score("anything", "") == 0.0


# --- not clicking ourselves ------------------------------------------------


def test_our_own_windows_are_excluded():
    """The bar displays the phrase being searched for, so it would match."""
    bar = [(1380, 790, 1900, 930)]
    inside = (1400, 840, 1500, 860)
    outside = (200, 300, 300, 320)
    assert _excluded(inside, bar) is True
    assert _excluded(outside, bar) is False


def test_nothing_is_excluded_when_we_have_no_windows():
    assert _excluded((10, 10, 20, 20), []) is False


# --- choosing between candidates -------------------------------------------


def test_target_centre_and_area():
    target = Target("x", 100, 200, 140, 220, score=90, source="uia")
    assert target.centre == (120, 210)
    assert target.area == 800


def test_the_tightest_match_wins(monkeypatch):
    """A label's own control is meant, not the panel that contains it."""
    words = [
        ("Guest mode", (500, 900, 600, 920)),          # the button
        ("Guest mode and other options", (0, 800, 1900, 1000)),  # its container
    ]
    monkeypatch.setattr(screen, "_ocr_words", lambda: words)
    monkeypatch.setattr(screen.focus, "our_window_rects", list)
    found = screen._find_via_ocr("Guest mode")
    assert found is not None
    assert found.centre == (550, 910)


def test_ocr_refuses_when_nothing_matches(monkeypatch):
    monkeypatch.setattr(
        screen, "_ocr_words", lambda: [("Downloads", (0, 0, 80, 20))]
    )
    monkeypatch.setattr(screen.focus, "our_window_rects", list)
    assert screen._find_via_ocr("AD JAYANTAN") is None


def test_ocr_skips_matches_inside_our_own_windows(monkeypatch):
    monkeypatch.setattr(
        screen, "_ocr_words", lambda: [("AD JAYANTAN", (1400, 840, 1500, 860))]
    )
    monkeypatch.setattr(
        screen.focus, "our_window_rects", lambda: [(1380, 790, 1900, 930)]
    )
    assert screen._find_via_ocr("AD JAYANTAN") is None


def test_find_prefers_the_accessibility_tree(monkeypatch):
    """Exact, fast, and a real control rather than a picture of one."""
    uia_hit = Target("Edit", 130, 105, 155, 120, score=100, source="uia")
    monkeypatch.setattr(screen, "_find_via_uia", lambda _l: uia_hit)
    monkeypatch.setattr(
        screen, "_find_via_ocr", lambda _l: pytest.fail("should not reach OCR")
    )
    assert screen.find("Edit") is uia_hit


def test_find_falls_back_to_ocr(monkeypatch):
    """Chrome's profile picker is invisible to the accessibility tree."""
    ocr_hit = Target("AD JAYANTAN", 520, 530, 720, 560, score=100, source="ocr")
    monkeypatch.setattr(screen, "_find_via_uia", lambda _l: None)
    monkeypatch.setattr(screen, "_find_via_ocr", lambda _l: ocr_hit)
    assert screen.find("AD JAYANTAN") is ocr_hit


def test_find_ignores_an_empty_label():
    assert screen.find("") is None
    assert screen.find("   ") is None


# --- the skill -------------------------------------------------------------


def test_clicking_a_label_moves_and_clicks(router, no_real_input, monkeypatch):
    monkeypatch.setattr(
        screen, "find",
        lambda _l: Target("AD JAYANTAN", 520, 530, 720, 560, score=100, source="ocr"),
    )
    result = router.dispatch("choose AD JAYANTAN in chrome")
    assert result.ok
    assert result.data["intent"] == "click_text"
    assert ("move", (620, 545), {}) in no_real_input
    assert any(c[0] == "click" for c in no_real_input)


def test_a_label_that_is_not_there_fails_without_clicking(
    router, no_real_input, monkeypatch
):
    monkeypatch.setattr(screen, "find", lambda _l: None)
    result = router.dispatch("choose AD JAYANTAN")
    assert result.ok is False
    assert "can't see" in result.say
    assert no_real_input == []


@pytest.mark.parametrize(
    "phrase,expected",
    [
        # everything specific still wins; this is only what is left over
        ("select all", "select_all"),
        ("press enter", "press_key"),
        ("click", "click"),
        ("click at 400 300", "click_at"),
        ("double click", "click"),
        ("scroll down", "scroll"),
        ("type hello", "type_text"),
        ("open chrome", "open_app"),
        # ...and these reach it
        ("choose AD JAYANTAN in chrome", "click_text"),
        ("click Sign in", "click_text"),
        ("select Guest mode", "click_text"),
    ],
)
def test_intent_precedence(router, phrase, expected):
    from jarvis.nlp.matcher import normalize

    text = normalize(phrase)
    hit = next((i.name for i in router.intents if i.match(text)), "-")
    assert hit == expected
