"""Finding a thing on screen by the words printed on it.

"Choose AD JAYANTAN" needs a target, and a coordinate is no use — the profile
card is wherever Chrome decided to draw it. Two ways to ask the machine where
something is, used in order:

1. **UI Automation.** Windows' accessibility tree. Exact, fast, and it returns
   a real control rather than a picture of one. It covers Explorer, Settings,
   Office, and most native software — but only what an application chooses to
   publish. Chrome's own profile picker, for instance, publishes nine elements
   and none of them are the profiles.

2. **OCR.** Windows has an offline recogniser built in (`Windows.Media.Ocr`),
   so this needs no Tesseract and no network: a screenshot in, words and their
   rectangles out, about 190 ms for a 1920x1080 desktop. It sees whatever is
   drawn, which is the point — Chrome's profile cards included.

Jarvis's own orb and command bar are on screen too, and the bar displays the
very phrase being searched for, so anything inside our own windows is excluded
before matching. Without that, "click open settings" would find Jarvis quoting
the user back at them and click that.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass

from jarvis.core import focus

log = logging.getLogger("jarvis.screen")

AVAILABLE = sys.platform == "win32"

# A candidate must reach this to be accepted, out of 100. Below it, clicking
# would be a guess, and a wrong click somewhere on the desktop is worse than
# saying "I can't see that".
MATCH_FLOOR = 70


@dataclass
class Target:
    """Something found on screen, and where to click it."""

    text: str
    left: int
    top: int
    right: int
    bottom: int
    score: float
    source: str  # "uia" or "ocr"

    @property
    def centre(self) -> tuple[int, int]:
        return ((self.left + self.right) // 2, (self.top + self.bottom) // 2)

    @property
    def area(self) -> int:
        return max(0, self.right - self.left) * max(0, self.bottom - self.top)


def find(label: str) -> Target | None:
    """Locate `label` on screen, preferring the accessibility tree."""
    label = (label or "").strip()
    if not label or not AVAILABLE:
        return None

    target = _find_via_uia(label)
    if target is not None:
        log.debug("found %r via UIA at %s", label, target.centre)
        return target

    target = _find_via_ocr(label)
    if target is not None:
        log.debug("found %r via OCR at %s (score %.0f)", label, target.centre, target.score)
    else:
        log.debug("could not find %r on screen", label)
    return target


# --- scoring ---------------------------------------------------------------

def _score(query: str, candidate: str) -> float:
    """How well `candidate` answers `query`, 0-100."""
    query, candidate = query.strip().lower(), candidate.strip().lower()
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 100.0
    try:
        from rapidfuzz import fuzz
    except ImportError:
        return 90.0 if query in candidate else 0.0

    # token_set handles word order and extra words on the label -- "AD JAYANTAN"
    # inside "AD JAYANTAN Profile 3" -- which plain ratio punishes heavily.
    score = fuzz.token_set_ratio(query, candidate)

    # partial_ratio only when the candidate is long enough to contain the query.
    # Applied blindly it scores any single letter of the query at 100: searching
    # for "ZEPHYRQUOKKA" matched a stray "Q" in a toolbar, perfectly, and would
    # have clicked it.
    if len(candidate) >= len(query) * 0.8:
        score = max(score, fuzz.partial_ratio(query, candidate))

    # Both of those are generous in the same direction: they reward a candidate
    # whose words are a *subset* of the query. Asked to "click conform and
    # continue" on a page holding a "Confirm and continue" button, the word
    # "and" scored 100 and the button 95, so Jarvis clicked the word "and".
    #
    # Extra words on the label are fine -- that is the "Profile 3" case. Missing
    # words are not: a fragment is not the thing asked for. So the score is
    # scaled by how much of the *query* the candidate actually accounts for.
    return score * _coverage(fuzz, query, candidate)


def _coverage(fuzz, query: str, candidate: str) -> float:
    """What fraction of the query's words the candidate accounts for, 0-1.

    Fuzzy per word, so a misspelling costs nothing: "conform" is covered by
    "confirm". Short filler words are ignored on their own but still count
    when the query is nothing but them.
    """
    words = [w for w in query.split() if w]
    if not words:
        return 1.0
    meaningful = [w for w in words if len(w) > 2] or words
    labels = [w for w in candidate.split() if w] or [candidate]
    def present(word: str) -> bool:
        if max(fuzz.ratio(word, label) for label in labels) >= 80:
            return True
        # Against the whole label too, so a query written as one word still
        # matches a label that spaces it out: "ZEPHYRQUOKKA" is covered by
        # "Zephyr Quokka", which per-word comparison alone would miss.
        return len(word) > 5 and fuzz.partial_ratio(word, candidate) >= 85

    return sum(1 for w in meaningful if present(w)) / len(meaningful)


def _excluded(box: tuple[int, int, int, int], ours: list[tuple[int, int, int, int]]) -> bool:
    """True when this rectangle's centre sits inside one of our own windows."""
    cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
    return any(left <= cx <= right and top <= cy <= bottom
               for left, top, right, bottom in ours)


# --- accessibility tree ----------------------------------------------------

def _uia():
    try:
        from comtypes.client import CreateObject, GetModule

        GetModule("UIAutomationCore.dll")
        from comtypes.gen.UIAutomationClient import CUIAutomation, IUIAutomation

        return CreateObject(CUIAutomation, interface=IUIAutomation)
    except Exception as exc:
        log.debug("UI Automation unavailable: %s", exc)
        return None


def _find_via_uia(label: str) -> Target | None:
    uia = _uia()
    if uia is None:
        return None
    try:
        hwnd = focus.foreground()
        root = uia.ElementFromHandle(hwnd) if hwnd else uia.GetRootElement()
        if root is None:
            return None
        TREE_SUBTREE = 7
        elements = root.FindAll(TREE_SUBTREE, uia.CreateTrueCondition())
    except Exception as exc:
        log.debug("UIA search failed: %s", exc)
        return None

    ours = focus.our_window_rects()
    best: Target | None = None
    for index in range(elements.Length):
        try:
            element = elements.GetElement(index)
            name = element.CurrentName or ""
            if not name:
                continue
            score = _score(label, name)
            if score < MATCH_FLOOR:
                continue
            r = element.CurrentBoundingRectangle
            box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue  # offscreen or collapsed
            if _excluded(box, ours):
                continue
            candidate = Target(name, *box, score=score, source="uia")
            # Prefer the better match, then the smaller control: the tightest
            # thing carrying the name is the thing meant, not its container.
            if best is None or (score, -candidate.area) > (best.score, -best.area):
                best = candidate
        except Exception:
            continue
    return best


# --- optical character recognition -----------------------------------------

def _ocr_words() -> list[tuple[str, tuple[int, int, int, int]]]:
    """Every word Windows can read on the screen right now, with its box."""
    try:
        import pyautogui
        from winsdk.windows.graphics.imaging import BitmapDecoder
        from winsdk.windows.media.ocr import OcrEngine
        from winsdk.windows.storage import FileAccessMode, StorageFile
    except ImportError as exc:
        log.debug("screen OCR unavailable: %s", exc)
        return []

    from jarvis.config import USER_DIR

    shot = USER_DIR / "screen.png"
    try:
        pyautogui.screenshot().save(shot)
    except Exception as exc:
        log.debug("screenshot failed: %s", exc)
        return []

    async def read():
        handle = await StorageFile.get_file_from_path_async(str(shot))
        stream = await handle.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            return None
        return await engine.recognize_async(bitmap)

    try:
        result = asyncio.run(read())
    except Exception as exc:
        log.debug("OCR failed: %s", exc)
        return []
    if result is None:
        return []

    words: list[tuple[str, tuple[int, int, int, int]]] = []
    for line in result.lines:
        for word in line.words:
            r = word.bounding_rect
            words.append(
                (word.text, (int(r.x), int(r.y), int(r.x + r.width), int(r.y + r.height)))
            )
        # The whole line is a candidate too, so a multi-word label matches as
        # one thing rather than as its least ambiguous word.
        boxes = [w.bounding_rect for w in line.words]
        if boxes:
            left = int(min(b.x for b in boxes))
            top = int(min(b.y for b in boxes))
            right = int(max(b.x + b.width for b in boxes))
            bottom = int(max(b.y + b.height for b in boxes))
            words.append((line.text, (left, top, right, bottom)))
    return words


def _find_via_ocr(label: str) -> Target | None:
    ours = focus.our_window_rects()
    best: Target | None = None
    for text, box in _ocr_words():
        if _excluded(box, ours):
            continue
        score = _score(label, text)
        if score < MATCH_FLOOR:
            continue
        candidate = Target(text, *box, score=score, source="ocr")
        if best is None or (score, -candidate.area) > (best.score, -best.area):
            best = candidate
    return best
