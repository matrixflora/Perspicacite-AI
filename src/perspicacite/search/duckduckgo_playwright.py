"""General-web search provider via headless Chromium + DuckDuckGo.

Distinct from the academic providers (``GoogleScholarPlaywrightProvider``,
``SemanticScholarSearch``, etc.) — this one returns raw web results
(title / url / snippet), NOT ``Paper`` objects. Intended for software
docs sites, GitHub repos, vendor blogs, README pages, and the rest of
the non-academic web.

Uses DuckDuckGo's HTML interface (``https://html.duckduckgo.com/html/``)
because it's the most stable contract: no API key, no JS required (so
parsing is simple), and the response shape doesn't change as often as
the JS-rendered Google results page. We still drive it through
Playwright so the response is rendered + the same browser fingerprint
applies as the rest of the Playwright providers.

Public surface mirrors ``google_scholar_playwright.py``:
``name``, ``description``, ``tier``, ``retry`` class-level attributes
and an ``async search(query, max_results, site_filter=None,
exclude_domains=None)`` coroutine. Tests replace
``_render_and_extract_results`` at module level to avoid a real browser.
"""
from __future__ import annotations

import asyncio
import contextlib
from typing import Any, ClassVar
from urllib.parse import parse_qs, quote, urlparse

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.search.duckduckgo_playwright")

_DDG_HTML_BASE = "https://html.duckduckgo.com/html/"

# Sentinel returned on detected bot-challenge pages (DuckDuckGo's
# anti-abuse interstitial). Lets the caller distinguish "blocked" from
# "no hits".
_BOT_CHALLENGE_SENTINEL: list[dict[str, str]] = []


def _build_search_url(
    query: str,
    *,
    site_filter: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Compose a DuckDuckGo HTML-search URL.

    `site_filter` adds OR'd ``site:domain`` operators (e.g.
    ``["github.com", "*.github.io"]``); `exclude_domains` adds
    ``-site:domain`` operators. Wildcards (``*.github.io``) are
    DuckDuckGo's native syntax.
    """
    parts: list[str] = [query.strip()]
    if site_filter:
        clauses = " OR ".join(f"site:{d}" for d in site_filter if d)
        if clauses:
            parts.append(f"({clauses})")
    if exclude_domains:
        for d in exclude_domains:
            if d:
                parts.append(f"-site:{d}")
    composed = " ".join(parts)
    return f"{_DDG_HTML_BASE}?q={quote(composed)}"


def _unwrap_ddg_redirect(href: str) -> str:
    """DuckDuckGo wraps result links in ``/l/?uddg=<encoded_url>``.

    Extract the underlying URL. Returns the input untouched when the
    pattern doesn't apply (e.g. an absolute URL DDG already exposed).
    """
    if not href:
        return ""
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc or parsed.path.startswith("/l/"):
        qs = parse_qs(parsed.query)
        for key in ("uddg", "url", "u"):
            vals = qs.get(key)
            if vals:
                return vals[0]
    return href


async def _render_and_extract_results(
    url: str,
    *,
    delay: float,
    headless: bool,
    user_agent: str,
) -> list[dict[str, str]]:
    """Launch Chromium, navigate to ``url``, return list of result dicts.

    Each dict has keys: ``title``, ``url``, ``snippet``. Returns ``[]``
    when Playwright isn't installed or on any error. Returns
    ``_BOT_CHALLENGE_SENTINEL`` when DuckDuckGo serves its anti-abuse
    interstitial.

    Single Playwright seam — tests mock this function to bypass the
    browser entirely.
    """
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "duckduckgo_playwright_missing",
            hint="uv pip install -e '[browser]' && playwright install chromium",
        )
        return []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            try:
                ctx = await browser.new_context(user_agent=user_agent)
                page = await ctx.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                await asyncio.sleep(delay)

                # Bot-challenge detection. DuckDuckGo HTML rarely shows
                # this but it can on aggressive use; treat as a soft
                # failure (return sentinel; caller decides).
                html = await page.content()
                if "anomaly detected" in html.lower() or "DuckDuckGo robot challenge" in html:
                    logger.warning("duckduckgo_bot_challenge", url=url[:100])
                    return _BOT_CHALLENGE_SENTINEL

                results: list[dict[str, str]] = []
                # The HTML interface renders each result as
                # <div class="result results_links results_links_deep web-result">
                # with .result__a (title link) and .result__snippet inside.
                for card_el in await page.query_selector_all("div.result"):
                    link_el = await card_el.query_selector("a.result__a")
                    if not link_el:
                        continue
                    title = (await link_el.inner_text()).strip()
                    raw_href = (await link_el.get_attribute("href")) or ""
                    href = _unwrap_ddg_redirect(raw_href)

                    snippet = ""
                    snip_el = await card_el.query_selector("a.result__snippet, div.result__snippet")
                    if snip_el:
                        snippet = (await snip_el.inner_text()).strip()

                    if title and href:
                        results.append(
                            {"title": title, "url": href, "snippet": snippet}
                        )
                return results
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()
    except Exception as exc:
        logger.warning("duckduckgo_render_failed", error=str(exc), url=url[:100])
        return []


class DuckDuckGoPlaywrightProvider:
    """General-web search via DuckDuckGo HTML interface + headless Chromium.

    Unlike the academic providers, this one returns ``list[dict]`` with
    ``title`` / ``url`` / ``snippet`` keys — NOT ``Paper`` objects. The
    MCP wrapper (``general_web_search``) handles the LLM-judge
    relevance pipeline; this provider just fetches.

    Uses ``tier = "flaky"`` so the caller treats single failures as
    soft. No retry — DuckDuckGo's anti-abuse is conservative.
    """

    name: ClassVar[str] = "duckduckgo"
    description: ClassVar[str] = (
        "General-web search via DuckDuckGo HTML interface (browser extra required)"
    )
    domains: ClassVar[list[str]] = ["general_web"]
    tier: ClassVar[str] = "flaky"
    retry: ClassVar[int] = 0

    def __init__(
        self,
        *,
        delay_seconds: float = 1.5,
        headless: bool = True,
        user_agent: str = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    ) -> None:
        self._delay = delay_seconds
        self._headless = headless
        self._user_agent = user_agent

    async def search(
        self,
        query: str,
        max_results: int = 10,
        *,
        site_filter: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        **_: Any,
    ) -> list[dict[str, str]]:
        """Fetch DuckDuckGo HTML results for ``query``.

        ``site_filter`` and ``exclude_domains`` are translated to
        ``site:`` / ``-site:`` operators on DuckDuckGo's side (wildcards
        like ``*.github.io`` are supported by DDG natively).
        """
        url = _build_search_url(
            query,
            site_filter=site_filter,
            exclude_domains=exclude_domains,
        )
        results = await _render_and_extract_results(
            url,
            delay=self._delay,
            headless=self._headless,
            user_agent=self._user_agent,
        )
        if results is _BOT_CHALLENGE_SENTINEL:
            return []
        # Cap to max_results
        sliced = results[:max_results]
        logger.info(
            "duckduckgo_search_done",
            query=query[:80],
            site_filter=site_filter,
            returned=len(sliced),
        )
        return sliced
