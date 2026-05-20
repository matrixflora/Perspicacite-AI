"""Google Scholar search via SerpApi's google_scholar engine.

Reliable, paid alternative to headless-Chromium scraping
(GoogleScholarPlaywrightProvider). SerpApi owns the proxies / CAPTCHA
solving, so this returns structured JSON — including a clean citation
count, which is the signal we want for relevance ranking.

``GoogleScholarChainProvider`` is the ``google_scholar`` slot used by
DomainAwareAggregator: it tries SerpApi first, then falls back to the
existing Playwright provider (which itself falls back to OpenRouter/Exa).
"""

from __future__ import annotations

import os
import re
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.serpapi_scholar")

_BASE_URL = "https://serpapi.com/search.json"
# 4-digit year (1900–2099) embedded in the publication_info summary, e.g.
# "MG Bellemare, Y Naddaf… - Journal of AI…, 2013 - jair.org".
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")


class SerpApiScholarProvider:
    """Google Scholar via SerpApi (reliable, structured, paid)."""

    name: ClassVar[str] = "google_scholar"
    description: ClassVar[str] = "Google Scholar via SerpApi (structured, reliable)"
    domains: ClassVar[list[str]] = ["general"]  # broad coverage across domains
    tier: ClassVar[str] = "external"  # network API → 1.5× base timeout
    retry: ClassVar[int] = 1

    def __init__(self, api_key: str = "") -> None:
        self._api_key = (
            api_key
            or os.getenv("SERPAPI_API_KEY")
            or os.getenv("SERPAPI_KEY")
            or ""
        ).strip()

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        if not self._api_key:
            logger.info("serpapi_scholar_no_key_skipped")
            return []

        params: dict[str, Any] = {
            "engine": "google_scholar",
            "q": query,
            "api_key": self._api_key,
            "num": min(max(max_results, 1), 20),  # Scholar caps at 20/page
            "hl": "en",
        }
        if year_min:
            params["as_ylo"] = year_min
        if year_max:
            params["as_yhi"] = year_max

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning(
                    "serpapi_scholar_error", error=str(exc), query=query[:80]
                )
                return []

        # SerpApi reports quota/key problems in a top-level "error" field
        # with HTTP 200, so check it explicitly.
        if isinstance(data, dict) and data.get("error"):
            logger.warning("serpapi_scholar_api_error", error=str(data["error"])[:200])
            return []

        papers: list[Paper] = []
        for item in data.get("organic_results") or []:
            title = (item.get("title") or "").strip()
            if not title:
                continue

            pub_info = item.get("publication_info") or {}
            summary = pub_info.get("summary") or ""

            authors: list[Author] = []
            for a in pub_info.get("authors") or []:
                nm = (a.get("name") or "").strip()
                if nm:
                    authors.append(Author(name=nm))
            # Fall back to the leading author chunk of the summary line when
            # SerpApi doesn't return a structured authors list.
            if not authors and summary:
                lead = summary.split(" - ")[0]
                for nm in lead.split(","):
                    nm = nm.strip()
                    if nm and not _YEAR_RE.fullmatch(nm):
                        authors.append(Author(name=nm))

            year: int | None = None
            m = _YEAR_RE.search(summary)
            if m:
                year = int(m.group(0))

            citation_count: int | None = None
            cited = (item.get("inline_links") or {}).get("cited_by") or {}
            if isinstance(cited.get("total"), int):
                citation_count = cited["total"]

            pdf_url: str | None = None
            for res in item.get("resources") or []:
                if (res.get("file_format") or "").upper() == "PDF" and res.get("link"):
                    pdf_url = res["link"]
                    break

            link = item.get("link") or None
            paper_id = link or f"serpapi:{item.get('result_id', title[:40])}"

            papers.append(
                Paper(
                    id=paper_id,
                    title=title,
                    authors=authors,
                    year=year,
                    abstract=item.get("snippet") or None,
                    url=link,
                    pdf_url=pdf_url,
                    citation_count=citation_count,
                    source=PaperSource.GOOGLE_SCHOLAR,
                    metadata={"provider": "serpapi", "summary": summary},
                )
            )

        logger.info("serpapi_scholar_search", query=query[:80], results=len(papers))
        return papers


class GoogleScholarChainProvider:
    """The ``google_scholar`` aggregator slot: try backends in order.

    Returns the first backend that yields results. Used to put the reliable
    SerpApi provider first and the Playwright provider (which itself falls
    back to OpenRouter/Exa) as backup, so a SerpApi outage or quota
    exhaustion still produces Scholar results.
    """

    name: ClassVar[str] = "google_scholar"
    description: ClassVar[str] = "Google Scholar (SerpApi primary, Playwright backup)"
    domains: ClassVar[list[str]] = ["general"]
    tier: ClassVar[str] = "external"
    retry: ClassVar[int] = 0  # each backend manages its own retries

    def __init__(self, backends: list[Any]) -> None:
        # Ordered; first non-empty result wins.
        self._backends = [b for b in backends if b is not None]

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **kwargs: Any,
    ) -> list[Paper]:
        for backend in self._backends:
            try:
                papers = await backend.search(
                    query,
                    max_results=max_results,
                    year_min=year_min,
                    year_max=year_max,
                    **kwargs,
                )
            except Exception as exc:
                logger.warning(
                    "google_scholar_backend_failed",
                    backend=type(backend).__name__,
                    error=str(exc),
                )
                continue
            if papers:
                logger.info(
                    "google_scholar_backend_used",
                    backend=type(backend).__name__,
                    results=len(papers),
                )
                return papers
        logger.info("google_scholar_all_backends_empty")
        return []
