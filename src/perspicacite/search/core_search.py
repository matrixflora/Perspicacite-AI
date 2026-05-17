"""CORE API v3 open-access search provider."""

from __future__ import annotations

import contextlib
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.core")

_CORE_API = "https://api.core.ac.uk/v3/search/works"


class CORESearchProvider:
    """Searches CORE — a cross-domain open-access aggregator (230M+ papers)."""

    name = "core"
    description = "CORE open-access aggregator search (free, optional API key)"
    domains: ClassVar[list[str]] = ["general"]
    tier: str = "reliable"
    retry: int = 0

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or None

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        filters: dict[str, Any] = {}
        if year_min:
            filters.setdefault("yearPublished", {})["$gte"] = year_min
        if year_max:
            filters.setdefault("yearPublished", {})["$lte"] = year_max

        payload: dict[str, Any] = {
            "q": query,
            "limit": min(max_results, 100),
        }
        if filters:
            payload["filters"] = filters

        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                resp = await client.post(_CORE_API, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("core_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for item in data.get("results") or []:
            doi = item.get("doi") or None
            paper_id = doi or f"core:{item.get('id', 'unknown')}"

            authors: list[Author] = []
            for a in item.get("authors") or []:
                name = (a.get("name") or "").strip()
                if name:
                    authors.append(Author(name=name))

            year: int | None = None
            with contextlib.suppress(KeyError, ValueError, TypeError):
                year = int(item["yearPublished"])

            journals = item.get("journals") or []
            journal = journals[0].get("title") if journals else None

            papers.append(
                Paper(
                    id=paper_id,
                    title=item.get("title") or "Untitled",
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=item.get("abstract"),
                    journal=journal,
                    pdf_url=item.get("downloadUrl"),
                    source=PaperSource.CORE,
                    metadata={"core_id": item.get("id")},
                )
            )

        logger.info("core_search", query=query[:80], results=len(papers))
        return papers
