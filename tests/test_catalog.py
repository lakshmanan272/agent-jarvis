"""What the planner is allowed to see.

The catalog is shortlisted, because sending all 102 commands costs enough
tokens to rate-limit a free tier. Shortlisting is a filter, and a filter that
drops the only command that could have served turns a solvable request into
"I don't know how to ...". Both failures below were taken from the log.
"""
from __future__ import annotations

import pytest

from jarvis.config import Config
from jarvis.nlp.brain import SHORTLIST, Brain
from jarvis.skills import load_all_skills
from jarvis.skills.base import REGISTRY

pytest.importorskip("rapidfuzz")


@pytest.fixture(scope="module")
def catalog():
    load_all_skills()
    brain = Brain(Config().brain)
    return lambda text: brain._build_catalog(REGISTRY, text)


def _has(menu: str, example: str) -> bool:
    return any(line.startswith(f"- {example}") for line in menu.splitlines())


def test_a_typo_still_reaches_the_command_that_fits(catalog):
    """'selct krishna profile' answered UNKNOWN because click_text, whose
    first example is "choose AD JAYANTAN", ranked nowhere near it."""
    assert _has(catalog("selct krishna profile"), "choose AD JAYANTAN")


def test_ranking_uses_every_example_not_just_the_first(catalog):
    """click_text also lists "select Guest mode", which is the close one."""
    assert _has(catalog("select the guest mode option"), "choose AD JAYANTAN")


@pytest.mark.parametrize(
    "phrase",
    [
        "selct krishna profile",
        "volume 40",
        "make me a sandwich",
        "notepad open pannu",
        "what time is it",
        "",
    ],
)
def test_the_open_ended_commands_are_always_offered(catalog, phrase):
    """Ranking a catch-all off the menu removes the only possible answer."""
    menu = catalog(phrase)
    assert _has(menu, "choose AD JAYANTAN"), "click_text missing"
    assert _has(menu, "open chrome"), "open_app missing"
    assert _has(menu, "work out how to turn on dark mode"), "autopilot missing"


def test_the_shortlist_still_has_a_ceiling(catalog):
    """Pinning must not quietly send the whole catalogue every time."""
    for phrase in ("volume 40", "selct krishna profile", "make me a sandwich"):
        assert len(catalog(phrase).splitlines()) <= SHORTLIST


def test_the_closest_command_still_ranks_first(catalog):
    """Pinning adds to the tail; it must not displace the obvious answer."""
    lines = catalog("turn the volume up a bit").splitlines()
    assert "volume up" in lines[0]


def test_an_empty_query_lists_everything(catalog):
    assert len(catalog("").splitlines()) == len(REGISTRY)
