"""Web and browser control: search, open sites, tab management."""
from __future__ import annotations

import logging
import re
import urllib.parse
import webbrowser

from jarvis.core import actuator as act
from jarvis.skills.base import ActionResult, intent

log = logging.getLogger("jarvis.skills.web")

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "bing": "https://www.bing.com/search?q={q}",
    "duckduckgo": "https://duckduckgo.com/?q={q}",
    "github": "https://github.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "amazon": "https://www.amazon.in/s?k={q}",
    "stack overflow": "https://stackoverflow.com/search?q={q}",
    "chatgpt": "https://chatgpt.com/?q={q}",
    "claude": "https://claude.ai/new?q={q}",
}

SITES = {
    "youtube": "https://www.youtube.com",
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "github": "https://github.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "maps": "https://maps.google.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "linkedin": "https://www.linkedin.com",
    "reddit": "https://www.reddit.com",
    "netflix": "https://www.netflix.com",
    "prime video": "https://www.primevideo.com",
    "hotstar": "https://www.hotstar.com",
    "spotify": "https://open.spotify.com",
    "whatsapp web": "https://web.whatsapp.com",
    "chatgpt": "https://chatgpt.com",
    "claude": "https://claude.ai",
    "stack overflow": "https://stackoverflow.com",
    "wikipedia": "https://en.wikipedia.org",
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
}

# "open google dot com", "go to github.com/anthropics"
_DOMAIN = re.compile(r"^[\w-]+(?:\.[\w-]+)+(?:/\S*)?$")


def open_url(url: str) -> None:
    webbrowser.open(url, new=2, autoraise=True)


# YouTube embeds every result's id in the search page as "videoId":"...".
_VIDEO_ID = re.compile(r'"videoId":"([\w-]{11})"')


def first_youtube_video(query: str, timeout: float = 4.0) -> str | None:
    """The id of the first result for `query`, or None if it can't be found.

    "Play X" should play X, not present a page of things that might be X. There
    is no URL that opens the top result directly, so the search page is fetched
    and its first video id read out of the inline JSON -- no API key, no
    scraping library. The response is streamed and abandoned at the first match
    rather than downloaded whole: the id turns up in the first few hundred
    kilobytes of a page that runs to megabytes.

    Failure is not an error. The caller falls back to opening the search
    results, which is where this started.
    """
    try:
        import httpx

        with httpx.stream(
            "GET",
            "https://www.youtube.com/results",
            params={"search_query": query},
            timeout=timeout,
            follow_redirects=True,
            # Without a browser agent YouTube serves a consent interstitial
            # that carries no results.
            headers={"User-Agent": _BROWSER_UA, "Accept-Language": "en-US,en;q=0.9"},
        ) as response:
            response.raise_for_status()
            window = ""
            for chunk in response.iter_text():
                window += chunk
                match = _VIDEO_ID.search(window)
                if match:
                    return match.group(1)
                # Keep only enough tail to catch an id split across chunks.
                window = window[-64:]
    except Exception as exc:
        log.debug("could not resolve a video for %r: %s", query, exc)
    return None


@intent(
    r"^(?:search|google|look\s*up)\s+(?:for\s+)?(?P<query>.+?)"
    r"(?:\s+on\s+(?P<engine>google|youtube|bing|duckduckgo|github|wikipedia|"
    r"maps|amazon|stack overflow|chatgpt|claude))?$",
    r"^search\s+(?P<engine>youtube|github|amazon|maps|wikipedia)\s+for\s+(?P<query>.+)$",
    name="web_search",
    verbatim=True,
    priority=8,
    description="Search the web",
    examples=("search for python decorators", "search cats on youtube"),
)
def do_search(ctx, query: str = "", engine: str | None = None, **_) -> ActionResult:
    query = (query or "").strip()
    if not query:
        return ActionResult.fail("Search for what?")
    template = SEARCH_ENGINES.get((engine or "google").strip(), SEARCH_ENGINES["google"])
    open_url(template.format(q=urllib.parse.quote_plus(query)))
    return ActionResult(ok=True, say=f"Searching {engine or 'google'}.")


@intent(
    r"^(?:play)\s+(?P<query>.+?)(?:\s+on\s+youtube)?$",
    name="play_youtube",
    verbatim=True,
    priority=7,
    description="Play something on YouTube",
    examples=("play lofi beats", "play interstellar soundtrack on youtube"),
)
def do_play(ctx, query: str = "", **_) -> ActionResult:
    query = (query or "").strip()
    if not query:
        return ActionResult.fail("Play what?")
    video = first_youtube_video(query)
    if video:
        open_url(f"https://www.youtube.com/watch?v={video}")
        return ActionResult(ok=True, say=f"Playing {query}.", data={"video": video})
    # Could not resolve one; show the results rather than nothing.
    open_url(SEARCH_ENGINES["youtube"].format(q=urllib.parse.quote_plus(query)))
    return ActionResult(
        ok=True, say=f"Here are results for {query}.", detail="no single match"
    )


@intent(
    r"^(?:open|go\s+to|visit|browse)\s+(?P<site>[\w.\-/:]+\.[\w\-/:.?=&%#]+)$",
    name="open_url",
    verbatim=True,
    priority=9,
    description="Open a URL",
    examples=("open github.com", "go to news.ycombinator.com"),
)
def do_open_url(ctx, site: str = "", **_) -> ActionResult:
    site = site.strip()
    if not _DOMAIN.match(site) and not site.startswith("http"):
        return ActionResult.fail(f"{site} doesn't look like a site.")
    url = site if site.startswith("http") else f"https://{site}"
    open_url(url)
    return ActionResult(ok=True, say="Opening.", data={"url": url})


@intent(
    r"^(?:open|go\s+to|visit|browse)\s+(?P<site>youtube|gmail|drive|google drive|"
    r"twitter|instagram|facebook|linkedin|reddit|netflix|prime video|hotstar|"
    r"spotify|whatsapp web|chatgpt|claude|stack overflow|wikipedia|amazon|flipkart|"
    r"maps)$",
    name="open_site",
    priority=10,
    description="Open a well-known site",
    examples=("open youtube", "go to gmail"),
)
def do_open_site(ctx, site: str = "", **_) -> ActionResult:
    url = SITES.get(site.strip())
    if url is None:
        return ActionResult.fail(f"No bookmark for {site}.")
    open_url(url)
    return ActionResult(ok=True, say=f"Opening {site}.", data={"url": url})


@intent(r"^(?:new|open)\s+tab$", name="new_tab", priority=6)
def do_new_tab(ctx, **_) -> ActionResult:
    """Open a browser tab."""
    act.hotkey("ctrl", "t")
    return ActionResult(ok=True, say="")


@intent(r"^close\s+(?:the\s+)?tab$", name="close_tab", priority=6)
def do_close_tab(ctx, **_) -> ActionResult:
    """Close the current tab."""
    act.hotkey("ctrl", "w")
    return ActionResult(ok=True, say="")


@intent(r"^(?:reopen|restore)\s+(?:the\s+)?(?:last\s+)?tab$", name="reopen_tab", priority=6)
def do_reopen_tab(ctx, **_) -> ActionResult:
    """Reopen the last closed tab."""
    act.hotkey("ctrl", "shift", "t")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:next|previous|last)\s+tab$",
    r"^(?:go\s+to\s+)?tab\s+(?P<number>\d+)$",
    name="switch_tab",
    examples=('next tab',),
    priority=6,
    description="Move between browser tabs",
)
def do_switch_tab(ctx, number: str | None = None, **_) -> ActionResult:
    if number:
        n = max(1, min(int(number), 8))
        act.hotkey("ctrl", str(n))
    elif ctx.last_command.startswith(("previous", "last")):
        act.hotkey("ctrl", "shift", "tab")
    else:
        act.hotkey("ctrl", "tab")
    return ActionResult(ok=True, say="")


@intent(r"^(?:refresh|reload)(?:\s+(?:the\s+)?(?:page|tab))?$", name="refresh", priority=6)
def do_refresh(ctx, **_) -> ActionResult:
    """Reload the current page."""
    act.hotkey("ctrl", "r")
    return ActionResult(ok=True, say="")


@intent(r"^(?:go\s+)?back$", name="go_back", priority=6)
def do_back(ctx, **_) -> ActionResult:
    """Navigate back."""
    act.hotkey("alt", "left")
    return ActionResult(ok=True, say="")


@intent(r"^(?:go\s+)?forward$", name="go_forward", priority=6)
def do_forward(ctx, **_) -> ActionResult:
    """Navigate forward."""
    act.hotkey("alt", "right")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:find|search)\s+(?P<query>.+?)\s+(?:on|in)\s+(?:this\s+)?page$",
    r"^find\s+in\s+page\s+(?P<query>.+)$",
    name="find_in_page",
    examples=('find pricing on this page',),
    verbatim=True,
    priority=9,
    description="Ctrl+F for a phrase on the current page",
)
def do_find_in_page(ctx, query: str = "", **_) -> ActionResult:
    act.hotkey("ctrl", "f")
    act.type_text(query, paste_threshold=ctx.config.control.paste_threshold)
    act.press("enter")
    return ActionResult(ok=True, say="")


@intent(
    r"^(?:bookmark|save)\s+(?:this\s+)?page$",
    name="bookmark",
    examples=('bookmark this page',),
    priority=6,
    description="Bookmark the current page",
)
def do_bookmark(ctx, **_) -> ActionResult:
    act.hotkey("ctrl", "d")
    return ActionResult(ok=True, say="Bookmarked.")


@intent(
    r"^(?:incognito|private)(?:\s+(?:mode|window|tab))?$",
    name="incognito",
    description="Open a private browsing window",
)
def do_incognito(ctx, **_) -> ActionResult:
    act.hotkey("ctrl", "shift", "n")
    return ActionResult(ok=True, say="")
