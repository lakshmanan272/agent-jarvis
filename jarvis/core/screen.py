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

def _exact(query: str, candidate: str) -> int:
    """1 when the label is literally what was asked for, ignoring case.

    A tie-break, not a score. Eight Chrome profiles named Lakshman, Lakshman
    2007, lakshmanan, lakshmanan 2007, Lakshmanan and Lakshmanan CEA all score
    100 against "lakshmanan", and the previous tie-break -- smallest box --
    picked "Lakshman", a different person's profile. Where one of the tied
    labels is exactly the words spoken, that is the one meant.
    """
    return int(query.strip().casefold() == candidate.strip().casefold())


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
            # Prefer the better match, then the label that is exactly what was
            # said, then the smaller control: the tightest thing carrying the
            # name is the thing meant, not its container.
            if best is None or _rank(candidate, label) > _rank(best, label):
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
        if best is None or _rank(candidate, label) > _rank(best, label):
            best = candidate
    return best


def _rank(target: Target, query: str) -> tuple[float, int, int]:
    """Sort key for choosing between candidates that all cleared the floor."""
    return (target.score, _exact(query, target.text), -target.area)


# --- picking by position rather than by name -------------------------------

# UI Automation's control type ids for the things people count out loud.
CONTROL_TYPES = {
    "link": 50005,      # Hyperlink
    "button": 50000,
    "result": 50005,    # a search result is a link
    "item": 50007,      # ListItem
    "tab": 50018,       # TabItem
}


def find_nth(kind: str, index: int) -> Target | None:
    """The `index`-th `kind` on screen, counting in reading order.

    "Click the first link" names nothing that is written anywhere, so no
    amount of matching text can answer it -- the search for "first" found
    nothing and reported it, correctly and uselessly. This counts controls
    instead, which the accessibility tree can do exactly.

    OCR cannot serve here: it reads words, and has no idea which of them is a
    link. So this is UIA or nothing, and nothing is said plainly.
    """
    uia = _uia()
    # -1 is "the last one", which is only knowable after counting them all.
    if uia is None or index == 0 or index < -1:
        return None
    control_type = CONTROL_TYPES.get(kind.lower())
    if control_type is None:
        return None

    try:
        hwnd = focus.foreground()
        root = uia.ElementFromHandle(hwnd) if hwnd else uia.GetRootElement()
        if root is None:
            return None
        TREE_SUBTREE = 7
        elements = root.FindAll(TREE_SUBTREE, uia.CreateTrueCondition())
    except Exception as exc:
        log.debug("UIA enumeration failed: %s", exc)
        return None

    found = _collect(uia, elements, control_type)
    if not found:
        log.debug("no %s controls on screen", kind)
        return None
    if index == -1:
        return found[-1]
    if len(found) < index:
        log.debug("wanted %s %d of only %d", kind, index, len(found))
        return None
    return found[index - 1]


def _collect(_uia_unused, elements, control_type: int) -> list[Target]:
    """Every on-screen control of one type, in reading order."""
    ours = focus.our_window_rects()
    width, height = _screen_size()
    found: list[Target] = []
    for position in range(elements.Length):
        try:
            element = elements.GetElement(position)
            if element.CurrentControlType != control_type:
                continue
            r = element.CurrentBoundingRectangle
            box = (int(r.left), int(r.top), int(r.right), int(r.bottom))
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            # Scrolled-away content keeps its place in the tree with
            # coordinates far off the screen -- a VS Code window reported
            # links at y=-45966. Counting those makes "the first link" a
            # thing the user cannot see.
            if box[1] < 0 or box[0] < 0 or box[1] > height or box[0] > width:
                continue
            if _excluded(box, ours):
                continue
            found.append(
                Target(
                    (element.CurrentName or "").strip(),
                    *box,
                    score=100.0,
                    source="uia",
                )
            )
        except Exception:
            continue
    # Reading order, which is what "first" means to the person looking at it.
    found.sort(key=lambda t: (t.top, t.left))
    return found


def _screen_size() -> tuple[int, int]:
    try:
        import pyautogui

        return pyautogui.size()
    except Exception:
        return (1920, 1080)


# --- when neither the tree nor the text can answer -------------------------

# The model reads a scaled screen; full resolution costs tokens and latency
# without improving where it says to click.
VISION_WIDTH = 1280

VISION_SYSTEM = """You locate one thing in a screenshot of a Windows desktop.

The image is {w} by {h} pixels. Answer in the image's own pixels.

Reply with ONE JSON object and nothing else:
{{"found": true, "x": <int>, "y": <int>, "label": "what is written there"}}
or
{{"found": false, "why": "<one short sentence>"}}

Give the centre of the thing itself, not of the panel around it. If several
things could be meant, pick the one a person would, and say which in "label".
If it is genuinely not visible, say found false -- do not guess a coordinate."""


def find_by_vision(ctx, label: str) -> Target | None:
    """Ask a model where `label` is, when nothing else could find it.

    The accessibility tree only knows what an application publishes, and OCR
    only knows words that are drawn. Neither can answer "the third option" or
    "the drive with the least space free" -- there is no such text anywhere,
    and no control named that. A model looking at the picture can.

    Last resort by construction: it costs a screenshot leaving the machine
    and about a second and a half, so both cheaper routes run first.
    """
    if not getattr(ctx.config.control, "visual_fallback", False):
        return None

    from jarvis.nlp.brain import Brain

    brain = Brain(ctx.config.brain)
    if not brain.available:
        return None

    try:
        import io as _io

        import pyautogui

        shot = pyautogui.screenshot()
        full_width = shot.width
        if shot.width > VISION_WIDTH:
            shot.thumbnail((VISION_WIDTH, VISION_WIDTH))
        buffer = _io.BytesIO()
        shot.save(buffer, format="PNG")
    except Exception as exc:
        log.debug("could not capture the screen: %s", exc)
        return None

    scale = full_width / shot.width
    reply = brain.look(
        VISION_SYSTEM.format(w=shot.width, h=shot.height),
        f"Where is: {label}",
        buffer.getvalue(),
        max_tokens=300,
    )
    answer = _json_object(reply or "")
    if not answer or not answer.get("found"):
        log.debug("vision could not find %r: %s", label, (answer or {}).get("why"))
        return None

    try:
        x, y = int(answer["x"]), int(answer["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= shot.width and 0 <= y <= shot.height):
        log.debug("vision gave %d,%d, outside the image", x, y)
        return None

    real_x, real_y = int(x * scale), int(y * scale)
    box = (real_x - 1, real_y - 1, real_x + 1, real_y + 1)
    if _excluded(box, focus.our_window_rects()):
        # It found Jarvis's own bar, which is quoting the phrase back.
        log.debug("vision pointed inside our own window")
        return None
    return Target(
        str(answer.get("label") or label),
        *box,
        score=100.0,
        source="vision",
    )


def _json_object(raw: str) -> dict | None:
    """The JSON object out of a reply that may be fenced or chatty."""
    import json
    import re

    match = re.search(r"\{.*\}", raw or "", re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
