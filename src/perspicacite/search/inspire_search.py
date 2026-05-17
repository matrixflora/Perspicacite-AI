"""INSPIRE-HEP literature search provider."""

from __future__ import annotations

import contextlib
from typing import Any, ClassVar

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.inspire")

_BASE_URL = "https://inspirehep.net/api/literature"


class INSPIREHEPSearchProvider:
    """Searches INSPIRE-HEP — the authoritative physics bibliography."""

    name = "inspire"
    description = "INSPIRE-HEP high-energy physics bibliography (free REST API)"
    domains: ClassVar[list[str]] = ["physics"]
    tier: str = "reliable"
    retry: int = 0

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
            q = f"{q} de {y_min}--{y_max}"

        params: dict[str, Any] = {
            "q": q,
            "size": min(max_results, 100),
            "sort": "mostrecent",
            "fields": "titles,authors,publication_info,dois,arxiv_eprints,abstracts,texkeys",
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            try:
                resp = await client.get(_BASE_URL, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("inspire_search_error", error=str(exc), query=query[:80])
                return []

        papers: list[Paper] = []
        for hit in (data.get("hits") or {}).get("hits") or []:
            meta = hit.get("metadata") or {}

            titles = meta.get("titles") or []
            title = titles[0].get("title") if titles else "Untitled"

            authors: list[Author] = []
            for a in meta.get("authors") or []:
                name = (a.get("full_name") or "").strip()
                if name:
                    authors.append(Author(name=name))

            pub_info = meta.get("publication_info") or []
            year: int | None = None
            journal: str | None = None
            if pub_info:
                year_raw = pub_info[0].get("year")
                with contextlib.suppress(TypeError, ValueError):
                    year = int(year_raw)
                journal = pub_info[0].get("journal_title")

            dois = meta.get("dois") or []
            doi = dois[0].get("value") if dois else None

            arxiv_eprints = meta.get("arxiv_eprints") or []
            arxiv_id = arxiv_eprints[0].get("value") if arxiv_eprints else None

            abstracts = meta.get("abstracts") or []
            abstract = abstracts[0].get("value") if abstracts else None

            texkeys = meta.get("texkeys") or []
            texkey = texkeys[0] if texkeys else None

            fallback_id = f"arxiv:{arxiv_id}" if arxiv_id else f"inspire:{hit.get('id', 'unknown')}"
            paper_id = doi or fallback_id

            papers.append(
                Paper(
                    id=paper_id,
                    title=title or "Untitled",
                    authors=authors,
                    year=year,
                    doi=doi,
                    abstract=abstract,
                    journal=journal,
                    source=PaperSource.INSPIRE_HEP,
                    metadata={"arxiv_id": arxiv_id, "texkey": texkey, "inspire_id": hit.get("id")},
                )
            )

        logger.info("inspire_search", query=query[:80], results=len(papers))
        return papers
