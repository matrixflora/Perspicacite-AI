"""Europe PMC structured full-text source.

Wired into the STRUCTURED stage of unified.retrieve_paper_content after
PMC JATS and before arXiv HTML. Reuses the existing JATS extractors.
"""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.pipeline.download.base import PaperContent
from perspicacite.pipeline.download.pmc import (
    _extract_references_from_xml,
    _extract_sections_from_xml,
    _extract_text_from_xml,
)

logger = get_logger("perspicacite.pipeline.europepmc")

EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"


async def get_content_from_europepmc(
    *,
    doi: str | None,
    pmid: str | None,
    pmcid: str | None,
    http_client: httpx.AsyncClient,
    **_: Any,
) -> PaperContent | None:
    """Fetch fulltextXML from Europe PMC and parse it via the JATS extractors.

    Returns None when no usable full text is available (caller continues
    down the pipeline). Never raises.
    """
    source, ident = await _resolve_id(doi=doi, pmid=pmid, pmcid=pmcid, http_client=http_client)
    if not source or not ident:
        return None
    url = f"{EUROPEPMC_REST}/{source}/{ident}/fullTextXML"
    try:
        r = await http_client.get(url, timeout=30)
    except httpx.HTTPError as exc:
        logger.info("europepmc_fetch_failed", error=str(exc))
        return None
    if r.status_code != 200 or not r.content:
        return None
    try:
        full_text = _extract_text_from_xml(r.content)
        sections = _extract_sections_from_xml(r.content)
        references = _extract_references_from_xml(r.content)
    except Exception as exc:
        logger.info("europepmc_parse_failed", error=str(exc))
        return None
    if not (full_text or "").strip():
        return None
    return PaperContent(
        success=True,
        doi=doi or "",
        content_type="structured",
        full_text=full_text,
        sections=sections,
        references=references,
        abstract=None,
        content_source="europepmc",
        metadata={"europepmc_source": source, "europepmc_id": ident},
    )


async def _resolve_id(
    *,
    doi: str | None,
    pmid: str | None,
    pmcid: str | None,
    http_client: httpx.AsyncClient,
) -> tuple[str | None, str | None]:
    """Return (source, id) tuple Europe PMC needs for fullTextXML."""
    if pmcid:
        # The PMC source expects ids like "PMC123"
        ident = pmcid if str(pmcid).startswith("PMC") else f"PMC{pmcid}"
        return "PMC", ident
    if pmid:
        return "MED", str(pmid)
    if not doi:
        return None, None
    try:
        r = await http_client.get(
            f"{EUROPEPMC_REST}/search",
            params={"query": f"DOI:{doi}", "format": "json", "resultType": "lite", "pageSize": 1},
            timeout=15,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        hits = (data.get("resultList") or {}).get("result") or []
        if not hits:
            return None, None
        h = hits[0]
        src = h.get("source")
        ident = h.get("id") or h.get("pmcid") or h.get("pmid")
        if not src or not ident:
            return None, None
        return str(src), str(ident)
    except (httpx.HTTPError, ValueError):
        return None, None
