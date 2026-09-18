"""Clicking something by where it is, not by what it says.

"Click the first link" names nothing that is written anywhere on screen, so
matching text cannot answer it: the search looked for the word "first", did
not find it, and said so -- correct, and no use at all to someone pointing at
a page of search results. Counting controls is what answers that question, and
the accessibility tree can count them exactly.
"""
from __future__ import annotations

import pytest

from jarvis.core import screen
from jarvis.core.screen import Target


class FakeElement:
    def __init__(self, name, control_type, box):
        self.CurrentName = name
        self.CurrentControlType = control_type
        self.CurrentBoundingRectangle = type(
            "R", (), {"left": box[0], "top": box[1], "right": box[2], "bottom": box[3]}
        )()


class FakeElements:
    def __init__(self, items):
        self._items = items
        self.Length = len(items)

    def GetElement(self, index):
        return self._items[index]


LINK = 50005
BUTTON = 50000


@pytest.fixture
def tree(monkeypatch):
    """Install a fake accessibility tree for the foreground window."""

    def install(*elements):
        class FakeUia:
            def ElementFromHandle(self, _h):
                return FakeRoot()

            def GetRootElement(self):
                return FakeRoot()

            def CreateTrueCondition(self):
                return None

        class FakeRoot:
            def FindAll(self, _scope, _cond):
                return FakeElements(list(elements))

        monkeypatch.setattr(screen, "_uia", FakeUia)
        monkeypatch.setattr(screen.focus, "foreground", lambda: 1)
        monkeypatch.setattr(screen.focus, "our_window_rects", list)
        monkeypatch.setattr(screen, "_screen_size", lambda: (1920, 1080))

    return install


def test_first_means_first_in_reading_order(tree):
    tree(
        FakeElement("bottom", LINK, (100, 800, 200, 820)),
        FakeElement("top", LINK, (100, 100, 200, 120)),
        FakeElement("middle", LINK, (100, 400, 200, 420)),
    )
    assert screen.find_nth("link", 1).text == "top"
    assert screen.find_nth("link", 2).text == "middle"
    assert screen.find_nth("link", 3).text == "bottom"


def test_two_on_the_same_row_read_left_to_right(tree):
    tree(
        FakeElement("right", LINK, (900, 100, 1000, 120)),
        FakeElement("left", LINK, (100, 100, 200, 120)),
    )
    assert screen.find_nth("link", 1).text == "left"


def test_last_is_the_last_one(tree):
    tree(
        FakeElement("a", LINK, (100, 100, 200, 120)),
        FakeElement("b", LINK, (100, 400, 200, 420)),
    )
    assert screen.find_nth("link", -1).text == "b"


def test_scrolled_away_controls_are_not_counted(tree):
    """A VS Code window reported links at y=-45966: still in the tree, long
    since scrolled out of sight. Counting those makes "the first link"
    something the user cannot see."""
    tree(
        FakeElement("scrolled off", LINK, (670, -45966, 800, -45946)),
        FakeElement("visible", LINK, (100, 500, 200, 520)),
    )
    assert screen.find_nth("link", 1).text == "visible"
    assert screen.find_nth("link", 2) is None


def test_our_own_windows_are_not_counted(tree, monkeypatch):
    tree(
        FakeElement("jarvis bar", LINK, (1400, 840, 1500, 860)),
        FakeElement("real", LINK, (100, 500, 200, 520)),
    )
    monkeypatch.setattr(
        screen.focus, "our_window_rects", lambda: [(1380, 790, 1900, 930)]
    )
    assert screen.find_nth("link", 1).text == "real"


def test_asking_past_the_end_is_a_miss_not_the_last_one(tree):
    tree(FakeElement("only", LINK, (100, 100, 200, 120)))
    assert screen.find_nth("link", 1) is not None
    assert screen.find_nth("link", 5) is None


def test_kinds_are_counted_separately(tree):
    tree(
        FakeElement("a button", BUTTON, (100, 100, 200, 120)),
        FakeElement("a link", LINK, (100, 400, 200, 420)),
    )
    assert screen.find_nth("link", 1).text == "a link"
    assert screen.find_nth("button", 1).text == "a button"


def test_an_unknown_kind_is_refused(tree):
    tree(FakeElement("x", LINK, (100, 100, 200, 120)))
    assert screen.find_nth("sandwich", 1) is None


def test_no_accessibility_tree_is_a_miss_not_a_crash(monkeypatch):
    monkeypatch.setattr(screen, "_uia", lambda: None)
    assert screen.find_nth("link", 1) is None


# --- the intent -------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,which,index",
    [
        ("click the first link", "first", 1),
        ("open the second result", "second", 2),
        ("click the 3rd link", "3rd", 3),
        ("select the last tab", "last", -1),
        ("click first link", "first", 1),
        ("open the tenth item", "tenth", 10),
    ],
)
def test_the_phrase_routes_and_carries_the_position(
    router, no_real_input, monkeypatch, phrase, which, index
):
    asked = {}

    def fake(kind, position):
        asked["kind"], asked["index"] = kind, position
        return Target("a link", 100, 200, 300, 220, score=100, source="uia")

    monkeypatch.setattr(screen, "find_nth", fake)
    result = router.dispatch(phrase)
    assert result.ok, result.say
    assert result.data["intent"] == "click_nth"
    assert asked["index"] == index
    assert ("move", (200, 210), {}) in no_real_input


def test_naming_a_label_still_goes_to_the_text_search(router):
    """click_nth must not swallow "click Sign in"."""
    from jarvis.nlp.matcher import normalize

    for phrase in ("click Sign in", "choose AD JAYANTAN", "select Guest mode"):
        hit = next((i.name for i in router.intents if i.match(normalize(phrase))), "-")
        assert hit == "click_text", phrase


def test_it_says_so_when_nothing_can_find_it(router, no_real_input, monkeypatch):
    """Only after looking at the screen has failed too."""
    tried = []
    monkeypatch.setattr(screen, "find_nth", lambda _k, _i: tried.append("tree"))
    monkeypatch.setattr(
        screen, "find_by_vision", lambda _ctx, _l: tried.append("vision")
    )
    result = router.dispatch("click the first link")
    assert result.ok is False
    assert tried == ["tree", "vision"], "both routes must be tried before failing"
    assert "Naming what it says" in result.detail
    assert no_real_input == []
