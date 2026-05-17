"""NASA Astrophysics Data System (ADS) search provider."""

from __future__ import annotations

import contextlib
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.ads")

_ADS_BASE = "https://api.adsabs.harvard.edu/v1/search/query"
_ADS_FIELDS = "title,author,year,doi,abstract,bibcode,identifier"


class ADSSearchProvider:
    """Searches NASA ADS — the authoritative astronomy bibliography."""

    name = "ads"
    description = "NASA ADS astronomy search (requires free ADS API token)"
    domains: ClassVar[list[str]] = ["astronomy"]
    tier: str = "external"
    retry: int = 1

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        q = query
        if year_min or year_max:
            y_min = year_min or 1900
            y_max = year_max or 2100
            q = f"{q} pubdate:[{y_min} TO {y_max}]"

        params: dict[str, Any] = {
            "q": q,
            "fl": _ADS_FIELDS,
            "rows": min(max_results, 200),
            "sort": "citation_count desc",
        }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                resp = await client.get(_ADS_BASE, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("ads_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for doc in (data.get("response") or {}).get("docs") or []:
            raw_title = doc.get("title") or []
            title = raw_title[0] if raw_title else "Untitled"

            authors: list[Author] = []
            for name in doc.get("author") or []:
                name = name.strip()
                if name:
                    authors.append(Author(name=name))

            year: int | None = None
            with contextlib.suppress(KeyError, ValueError, TypeError):
                year = int(doc["year"])

            raw_doi = doc.get("doi") or []
            doi = raw_doi[0] if raw_doi else None
            paper_id = doi or f"ads:{doc.get('bibcode', 'unknown')}"

            identifiers = doc.get("identifier") or []
            arxiv_id: str | None = None
            for ident in identifiers:
                if ident.startswith("arxiv:"):
                    arxiv_id = ident[6:]
                    break

            papers.append(
                Paper(
                    id=paper_id,
                    title=title,
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=doc.get("abstract"),
                    source=PaperSource.ADS,
                    metadata={
                        "bibcode": doc.get("bibcode"),
                        "arxiv_id": arxiv_id,
                    },
                )
            )

        logger.info("ads_search", query=query[:80], results=len(papers))
        return papers
