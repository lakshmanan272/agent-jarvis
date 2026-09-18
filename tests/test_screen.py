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


# --- a fragment is not the thing you asked for ------------------------------
# "click conform and continue" on a page holding a "Confirm and continue"
# button clicked the word "and" instead. Both token_set_ratio and
# partial_ratio reward a candidate whose words are a SUBSET of the query, so
# the three-letter word scored 100 and the button 95.


def _best(query, candidates):
    from jarvis.core.screen import MATCH_FLOOR, _score

    scored = [(c, _score(query, c)) for c in candidates]
    winner, score = max(scored, key=lambda pair: pair[1])
    return (winner, score) if score >= MATCH_FLOOR else (None, score)


def test_a_shared_word_does_not_beat_the_whole_label():
    winner, _score_value = _best(
        "conform and continue",
        ["and", "Confirm and continue", "continue", "Manage", "Confirm"],
    )
    assert winner == "Confirm and continue"


@pytest.mark.parametrize("fragment", ["and", "continue", "Confirm"])
def test_a_fragment_alone_is_not_good_enough(fragment):
    """Even with nothing else on screen, one word of three is not the button.
    Clicking the wrong thing is worse than reporting that it was not found."""
    winner, _score_value = _best("conform and continue", [fragment])
    assert winner is None


def test_extra_words_on_the_label_are_still_fine():
    """The other direction must keep working: the label may say more than
    was asked for, which is how a profile card reads."""
    winner, _s = _best("AD JAYANTAN", ["AD JAYANTAN Profile 3", "Krishna"])
    assert winner == "AD JAYANTAN Profile 3"


def test_a_one_word_query_still_matches_a_spaced_label():
    winner, _s = _best("ZEPHYRQUOKKA", ["Zephyr Quokka", "Q"])
    assert winner == "Zephyr Quokka"


def test_a_stray_letter_is_never_the_answer():
    assert _best("ZEPHYRQUOKKA", ["Q", "x", "."])[0] is None


def test_a_misspelling_still_finds_the_button():
    """Coverage is fuzzy per word, so "conform" is covered by "Confirm"."""
    winner, _s = _best("conform and continue", ["Confirm and continue"])
    assert winner == "Confirm and continue"


# --- choosing between labels that all match ---------------------------------
# A Chrome profile picker with Lakshman, Lakshman 2007, lakshmanan,
# lakshmanan 2007, Lakshmanan and Lakshmanan CEA on it: every one of those
# scores 100 against "lakshmanan". The tie-break used to be the smallest box,
# which picked "Lakshman" -- a different person's profile.

PROFILES = [
    "AD", "AD JAYANTAN", "Cseb", "Cseb Placement", "Krishna", "laksh",
    "laksh manan", "Lakshman", "Lakshman 2007", "lakshmanan",
    "lakshmanan 2007", "Lakshmanan CEA", "LIGETH", "Guest mode",
]


def _pick(query, labels=None):
    """The label `find` would settle on, without needing a real screen."""
    from jarvis.core.screen import MATCH_FLOOR, _exact, _score

    scored = [(c, _score(query, c)) for c in labels or PROFILES]
    viable = [(c, s) for c, s in scored if s >= MATCH_FLOOR]
    if not viable:
        return None
    return max(viable, key=lambda pair: (pair[1], _exact(query, pair[0])))[0]


@pytest.mark.parametrize(
    "spoken",
    ["lakshmanan", "Lakshman", "lakshmanan 2007", "Lakshman 2007",
     "Lakshmanan CEA", "laksh manan", "laksh", "Krishna", "AD JAYANTAN"],
)
def test_the_exact_name_wins_over_the_similar_ones(spoken):
    assert _pick(spoken) == spoken or _pick(spoken).casefold() == spoken.casefold()


def test_a_partial_name_no_longer_lands_on_a_different_profile():
    """Nothing is exactly "lakshman an", so this is a genuine near-miss --
    but it must not silently pick a stranger's profile either."""
    from jarvis.core.screen import _exact

    assert _exact("lakshmanan", "Lakshman") == 0
    assert _exact("lakshmanan", "lakshmanan") == 1
    assert _exact("LAKSHMANAN", "  lakshmanan  ") == 1, "case and space only"


def test_score_still_decides_before_exactness():
    """Exactness is a tie-break, not an override: a better-scoring label
    still wins even when a worse one happens to be exact."""
    from jarvis.core.screen import _score

    assert _score("guest mode", "Guest mode") > _score("guest mode", "Guest")
    assert _pick("guest mode") == "Guest mode"
