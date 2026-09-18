"""The optional model, used for two quite different jobs.

`plan` is the fallback for phrases the router missed. It rewrites loose natural
language into one canonical command the router already knows -- it does not
invent abilities, and the reply is checked against the command table before
anything runs.

`compose` is the opposite: the user asked for prose that does not exist yet
("write about Vijay"), and the model's answer *is* the deliverable. It is a
larger, slower call with a much wider output, so the two are kept separate
rather than one method with a mode flag.
"""
from __future__ import annotations

import logging

log = logging.getLogger("jarvis.brain")

# How many candidate commands to show the model. Enough that the right one
# is almost always present, few enough to stay inside a free tier's budget.
SHORTLIST = 18

# A rewrite may be a short chain, because a single spoken request often is
# one: "delete all the text" is select-all and then delete. Rewriting it to
# "select all" alone left the text on screen and still reported done.
# Bounded so a confused model cannot turn one phrase into a rampage.
MAX_PLAN_STEPS = 4

SYSTEM_PROMPT = """You translate a user's spoken request into commands from \
the list below. Reply with the command text only: no quotes, no explanation, \
no punctuation at the end. If nothing in the list fits, reply exactly: UNKNOWN

Most requests are one command. When the request genuinely needs more than one \
step, join them with " and " in the order they must run, up to {max_steps} \
steps. Never drop a step the user asked for: "delete all the text" is \
"select all and delete", not "select all".

Each entry below is an EXAMPLE, not a fixed string. The specific words in it \
-- app names, search terms, numbers, file names -- are blanks to fill with what \
the user actually asked for, and "fill in:" names them. So "open chrome" covers \
opening any application: for "notepad open pannu" answer "open notepad", and \
for "open omen gaming" answer "open omen gaming" -- never substitute the example's own words.

Available commands (with example phrasings):
{catalog}"""

COMPOSE_PROMPT = (
    "You are writing text that will be typed straight into a document on the "
    "user's screen. Produce only the text itself: no preamble, no "
    '"Here is...", no markdown formatting, no surrounding quotes. Plain prose '
    "in plain paragraphs. Keep it to roughly {words} words unless the request "
    "clearly asks for more or less."
)

# Writing a few hundred words takes longer than picking a command out of a
# list, so composing gets its own budget rather than the router's.
COMPOSE_TIMEOUT_S = 25.0


class Brain:
    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self._phrasing_cache: list[tuple[str, str]] = []

    @property
    def available(self) -> bool:
        if not self.cfg.enabled:
            return False
        if self.cfg.provider in ("anthropic", "openai"):
            return bool(self.cfg.key())
        return True  # ollama needs no credential

    def _phrasings(self, intents) -> list[tuple[str, str]]:
        """(example, description) for every intent, computed once.

        The description carries the intent's slot names where it has any.
        Without them the model reads each entry as a fixed string: shown only
        "open chrome" it answered UNKNOWN to "notepad open pannu" and, worse,
        rewrote "open omen gaming" to "open chrome" -- substituting the
        example itself rather than filling the blank in it.
        """
        if not self._phrasing_cache:
            self._phrasing_cache = [
                (
                    item.examples[0] if item.examples else item.name.replace("_", " "),
                    _describe(item),
                )
                for item in intents
            ]
        return self._phrasing_cache

    def _build_catalog(self, intents, text: str = "") -> str:
        """The menu shown to the model, narrowed to what could plausibly fit.

        All 91 commands cost about 1100 tokens a call. On a free tier rated at
        8000 tokens a minute that is roughly seven commands before everything
        starts coming back 429 — which is exactly what happened when this was
        measured. Ranking by similarity and sending only the closest
        `SHORTLIST` cuts it several-fold, and it improves the answer too: the
        model chooses between a dozen plausible commands instead of
        skim-reading ninety mostly irrelevant ones.
        """
        entries = self._phrasings(intents)
        if not text:
            return "\n".join(f"- {ex}  ({desc})" for ex, desc in entries)
        try:
            from rapidfuzz import fuzz
        except ImportError:
            entries = entries[:SHORTLIST]
        else:
            entries = sorted(
                entries,
                key=lambda e: max(
                    fuzz.token_set_ratio(text, e[0]),
                    fuzz.token_set_ratio(text, e[1]),
                ),
                reverse=True,
            )[:SHORTLIST]
        return "\n".join(f"- {ex}  ({desc})" for ex, desc in entries)

    def plan(self, text: str, intents) -> str | None:
        """Return a router-runnable command string, or None."""
        try:
            import httpx
        except ImportError:
            return None
        prompt = SYSTEM_PROMPT.format(
            catalog=self._build_catalog(intents, text), max_steps=MAX_PLAN_STEPS
        )
        reply = self._ask(httpx, prompt, text)
        if reply is None:
            return None
        reply = _unwrap(reply)
        if not reply or reply.upper().startswith("UNKNOWN"):
            return None
        # Guard against the model echoing the prompt or waffling. A chain of
        # four commands is longer than one, so the ceiling scales with it --
        # but a newline is still waffle, never a plan.
        if len(reply) > 120 * MAX_PLAN_STEPS or "\n" in reply:
            return None
        return reply

    def compose(self, request: str, words: int = 180) -> str | None:
        """Write the text the user asked for, ready to be typed.

        Unlike `plan`, there is nothing to validate the answer against: the
        prose *is* the result. So the guard rails are on the shape rather than
        the content -- a generous token budget, and a refusal to return
        something so short it is obviously an apology or an error message.
        """
        if not self.available:
            return None
        try:
            import httpx
        except ImportError:
            return None

        prompt = COMPOSE_PROMPT.format(words=words)
        reply = self._ask(
            httpx, prompt, request, max_tokens=words * 3, timeout=COMPOSE_TIMEOUT_S
        )
        if reply is None:
            return None

        text = (reply or "").strip().strip('"')
        if len(text) < 40:
            # Too short to be the essay that was asked for; almost always the
            # model declining or erroring in prose.
            log.info("compose returned %d chars, discarding", len(text))
            return None
        return text

    def _ask(
        self, httpx, system: str, text: str,
        max_tokens: int = 64, timeout: float | None = None,
    ) -> str | None:
        """Ask the model, falling back to a second service if the first will not.

        A free tier rate-limits, and when it does every command that needed
        the planner answers "I don't know how to ...". The fallback runs the
        identical path with a different config, so there is no second, less
        tested route to a model.
        """
        for cfg, label in self._providers():
            try:
                return _invoke(httpx, cfg, system, text, max_tokens, timeout)
            except Exception as exc:
                log.warning("%s call failed: %s", label, exc)
        return None

    def _providers(self):
        """The configs to try, in order. The fallback is optional."""
        chain = [(self.cfg, "brain")]
        fallback = self.cfg.fallback()
        if fallback is not None:
            chain.append((fallback, f"brain fallback ({fallback.model})"))
        return chain

    def _anthropic(
        self, httpx, system: str, text: str,
        max_tokens: int = 64, timeout: float | None = None,
    ) -> str:
        key = self.cfg.key()
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.cfg.model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": text}],
            },
            timeout=timeout or self.cfg.timeout_s,
        )
        response.raise_for_status()
        blocks = response.json().get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def _openai(
        self, httpx, system: str, text: str,
        max_tokens: int = 64, timeout: float | None = None,
    ) -> str:
        """Any service speaking the OpenAI chat format.

        One method covers Groq, OpenRouter, Together, Gemini's compatibility
        endpoint and a local llama.cpp server -- they differ only in `base_url`
        and the model name, so adding a provider is configuration, not code.
        """
        response = httpx.post(
            f"{self.cfg.base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.cfg.key()}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.cfg.model,
                "max_tokens": max_tokens,
                # Zero for a lookup; a little warmth when writing prose.
                "temperature": 0 if max_tokens <= 64 else 0.4,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
            timeout=timeout or self.cfg.timeout_s,
        )
        response.raise_for_status()
        choices = response.json().get("choices", [])
        return choices[0]["message"]["content"] if choices else ""

    def _ollama(self, httpx, system: str, text: str) -> str:
        response = httpx.post(
            f"{self.cfg.ollama_url}/api/chat",
            json={
                "model": self.cfg.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
            timeout=self.cfg.timeout_s,
        )
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")


def _invoke(httpx, cfg, system: str, text: str, max_tokens: int, timeout):
    """One model call, for whichever provider `cfg` names."""
    brain = Brain(cfg)
    if cfg.provider == "anthropic":
        return brain._anthropic(httpx, system, text, max_tokens, timeout)
    if cfg.provider == "openai":
        return brain._openai(httpx, system, text, max_tokens, timeout)
    return brain._ollama(httpx, system, text)


def _unwrap(reply: str) -> str:
    """Strip the decoration models put around a one-line answer.

    Gemini returns `start notepad` in backticks and sometimes in a fenced
    block; left in place the backticks reach the router as part of the
    command and nothing matches.
    """
    text = (reply or "").strip()
    if text.startswith("```"):
        lines = [ln for ln in text.splitlines() if not ln.startswith("```")]
        text = "\n".join(lines).strip()
    return text.strip('`').strip('"').strip("'").strip()


def _slots(intent) -> list[str]:
    """The named groups an intent captures: the parts a caller fills in."""
    names: list[str] = []
    for pattern in intent.patterns:
        for name in pattern.groupindex:
            if name not in names:
                names.append(name)
    return names


def _describe(intent) -> str:
    """The description a model sees, with the fill-in-the-blank parts named."""
    description = intent.description or intent.name
    slots = _slots(intent)
    if not slots:
        return description
    return f"{description}; fill in: {', '.join(slots)}"
