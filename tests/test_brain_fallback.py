"""When the first model refuses, a second one answers.

Measured need: a free tier rate-limits. Fifteen calls in one session came
back 429, and because there was nothing behind them every command that needed
the planner answered "I don't know how to ...". A refusal from one service is
not a reason to have no answer.
"""
from __future__ import annotations

import pytest

from jarvis.config import BrainConfig
from jarvis.nlp import brain as brain_module
from jarvis.nlp.brain import Brain, _unwrap


def _cfg(**kw) -> BrainConfig:
    base = {
        "enabled": True, "provider": "openai", "model": "primary-model",
        "base_url": "https://primary.example", "api_key": "x",
        "fallback_provider": "openai", "fallback_model": "fallback-model",
        "fallback_base_url": "https://fallback.example", "fallback_api_key": "y",
    }
    base.update(kw)
    return BrainConfig(**base)


@pytest.fixture
def attempts(monkeypatch):
    """Record which model each attempt used, and control what it returns."""
    seen: list[str] = []
    plan = {"primary": RuntimeError("429 Too Many Requests"), "fallback": "select all"}

    def fake_invoke(_httpx, cfg, _system, _text, _max_tokens, _timeout):
        seen.append(cfg.model)
        outcome = plan["primary" if cfg.model == "primary-model" else "fallback"]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(brain_module, "_invoke", fake_invoke)
    return seen, plan


def test_the_fallback_answers_when_the_first_refuses(attempts):
    seen, _plan = attempts
    assert Brain(_cfg()).plan("highlight the lot", []) == "select all"
    assert seen == ["primary-model", "fallback-model"]


def test_the_fallback_is_not_called_when_the_first_works(attempts):
    seen, plan = attempts
    plan["primary"] = "volume up"
    assert Brain(_cfg()).plan("louder", []) == "volume up"
    assert seen == ["primary-model"], "the fallback cost a call for nothing"


def test_no_fallback_configured_behaves_exactly_as_before(attempts):
    seen, _plan = attempts
    assert Brain(_cfg(fallback_provider="")).plan("anything", []) is None
    assert seen == ["primary-model"]


def test_both_failing_is_a_miss_not_a_crash(attempts):
    seen, plan = attempts
    plan["fallback"] = RuntimeError("also down")
    assert Brain(_cfg()).plan("anything", []) is None
    assert seen == ["primary-model", "fallback-model"]


def test_compose_gets_the_same_fallback(attempts):
    _seen, plan = attempts
    plan["fallback"] = "A paragraph long enough to pass the length guard " * 2
    assert Brain(_cfg()).compose("write about something") is not None


# --- models decorate their answers ------------------------------------------
# Gemini returns `start notepad` in backticks, sometimes fenced. Left in place
# the backticks reach the router as part of the command and nothing matches.


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("`start notepad`", "start notepad"),
        ("```\nnotepad\n```", "notepad"),
        ('"open chrome"', "open chrome"),
        ("  volume up  ", "volume up"),
        ("select all and delete", "select all and delete"),
    ],
)
def test_decoration_is_stripped(raw, want):
    assert _unwrap(raw) == want
