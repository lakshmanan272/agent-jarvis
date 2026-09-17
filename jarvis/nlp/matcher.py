"""Text normalisation and fuzzy lookup.

Speech recognisers produce lower-case, unpunctuated, occasionally mangled text.
Everything here exists to turn that into something regex patterns can match
without each skill re-implementing the same clean-up.
"""
from __future__ import annotations

import re
import unicodedata

try:
    from rapidfuzz import fuzz as _rf_fuzz
    from rapidfuzz import process as _rf_process
except ImportError:  # fuzzy matching degrades to exact/substring
    _rf_process = None
    _rf_fuzz = None

# Politeness and hesitation that carry no intent. Stripped before routing so
# "jarvis could you please open chrome" and "open chrome" hit the same pattern.
_FILLER = re.compile(
    r"\b(please|kindly|could you|can you|would you|i want to|i want you to|"
    r"i need you to|for me|just|now|uh+|um+|er+|like|okay|ok so)\b"
)
_JARVIS = re.compile(r"\b(hey |ok |okay )?jarvis\b[,:]?")
_PUNCT = re.compile(r"[^\w\s%+\-./:@\']")
_SPACES = re.compile(r"\s+")

# Recognisers spell digits out; commands read better with numerals.
_NUMBER_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60",
    "seventy": "70", "eighty": "80", "ninety": "90", "hundred": "100",
}

# Common recogniser mishearings, corrected before routing. Split in two because
# multi-word fixes have to be applied to the whole string, single-word ones
# per token (so "chrom" is fixed but "chromatic" is left alone).
_PHRASE_FIXES = {
    "vs code": "vscode",
    "note pad": "notepad",
    "you tube": "youtube",
    "what's app": "whatsapp",
    "whats app": "whatsapp",
    "google chrom": "google chrome",
    "screen shot": "screenshot",
    "space bar": "space",
    "full stop": "period",
}
_PHRASE_RE = re.compile(
    r"\b("
    + "|".join(re.escape(k) for k in sorted(_PHRASE_FIXES, key=len, reverse=True))
    + r")\b"
)
_WORD_FIXES = {
    "jervis": "jarvis", "javis": "jarvis", "jarvas": "jarvis",
    "clique": "click", "klick": "click", "scrawl": "scroll",
    "chrom": "chrome", "crome": "chrome", "explora": "explorer",
    "minimise": "minimize", "maximise": "maximize",
}


def normalize(text: str) -> str:
    """Canonicalise recogniser output for pattern matching."""
    text = unicodedata.normalize("NFKC", text).lower().strip()
    text = _PUNCT.sub(" ", text)
    text = _SPACES.sub(" ", text)
    # Phrase fixes first: they span token boundaries ("note pad" -> "notepad").
    text = _PHRASE_RE.sub(lambda m: _PHRASE_FIXES[m.group(1)], text)
    words = []
    for word in text.split():
        word = _WORD_FIXES.get(word, word)
        words.append(_NUMBER_WORDS.get(word, word))
    text = " ".join(words)
    text = _JARVIS.sub(" ", text)
    text = _FILLER.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


def strip_wake(text: str, wake_words: list[str]) -> tuple[bool, str]:
    """Split a phrase into (heard a wake word, the rest of the phrase)."""
    low = text.lower().strip()
    for wake in sorted(wake_words, key=len, reverse=True):
        wake = wake.lower()
        if low.startswith(wake):
            return True, low[len(wake) :].lstrip(" ,.")
        if wake in low:
            return True, low.split(wake, 1)[1].lstrip(" ,.")
    return False, low


def best_match(query: str, choices: dict[str, str], cutoff: int = 72) -> str | None:
    """Return the value whose key best matches `query`, or None below `cutoff`.

    `choices` maps alias -> canonical value, so several spellings can point at
    the same app or window.
    """
    if not query or not choices:
        return None
    query = query.strip().lower()
    if query in choices:
        return choices[query]
    for alias, value in choices.items():
        if query in alias or alias in query:
            return value
    if _rf_process is None:
        return None
    hit = _rf_process.extractOne(
        query, list(choices.keys()), scorer=_rf_fuzz.WRatio, score_cutoff=cutoff
    )
    return choices[hit[0]] if hit else None
