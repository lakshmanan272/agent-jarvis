"""Normalisation and fuzzy-lookup behaviour."""
from __future__ import annotations

import pytest

from jarvis.nlp.matcher import best_match, normalize, strip_wake


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Jarvis, open Chrome!", "open chrome"),
        ("hey jarvis  OPEN   chrome", "open chrome"),
        ("could you please click", "click"),
        ("um, scroll down", "scroll down"),
        ("press tab three times", "press tab 3 times"),
        ("open note pad", "open notepad"),
        ("open you tube", "open youtube"),
        ("volume to fifty", "volume to 50"),
        ("", ""),
    ],
)
def test_normalize(raw, expected):
    assert normalize(raw) == expected


def test_normalize_keeps_url_punctuation():
    assert normalize("go to github.com/anthropics") == "go to github.com/anthropics"


def test_strip_wake_finds_leading_wake_word():
    heard, rest = strip_wake("jarvis open chrome", ["jarvis", "hey jarvis"])
    assert heard and rest == "open chrome"


def test_strip_wake_prefers_the_longest_match():
    heard, rest = strip_wake("hey jarvis click", ["jarvis", "hey jarvis"])
    assert heard and rest == "click"


def test_strip_wake_reports_absence():
    heard, rest = strip_wake("open chrome", ["jarvis"])
    assert not heard and rest == "open chrome"


def test_best_match_exact_and_substring():
    choices = {"google chrome": "chrome", "visual studio code": "code"}
    assert best_match("google chrome", choices) == "chrome"
    assert best_match("chrome", choices) == "chrome"


def test_best_match_tolerates_misspelling():
    assert best_match("visual studio cod", {"visual studio code": "code"}) == "code"


def test_best_match_returns_none_below_cutoff():
    assert best_match("xylophone", {"visual studio code": "code"}) is None


def test_best_match_handles_empty_input():
    assert best_match("", {"a": "b"}) is None
    assert best_match("a", {}) is None
