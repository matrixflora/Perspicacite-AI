"""Parsing helpers for arXiv-style DOIs.

OpenAlex frequently indexes arXiv preprints with **no DOI link** and
**no arXiv ID** exposed in its ``ids`` block. The originally-shipped
fallback used ``filter=ids.arxiv:<id>`` — that filter does not exist
in OpenAlex (returns HTTP 400). The 2026-05-15 audit re-run uncovered
this; the correct chain is:

1. Parse the arXiv id out of the DOI (this module's
   :func:`parse_arxiv_doi`).
2. Resolve the arXiv id to the paper's title via
   ``https://export.arxiv.org/api/query`` (:func:`resolve_arxiv_title`).
3. Query OpenAlex ``filter=title.search:"<title>"`` for the canonical
   Work id.

Empirically (verified 2026-05-15 against real OpenAlex), exact-phrase
title.search returns a single high-confidence hit for arXiv preprints —
``W3098425262`` for the RAG paper, ``W2626778328`` for "Attention Is
All You Need" (6 538 citations).
"""
from __future__ import annotations

import re

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.arxiv_ids")

# Matches: 10.48550/arXiv.2005.11401  /  10.48550/arxiv.2305.12345v2
_ARXIV_DOI_RE = re.compile(
    r"^\s*10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)\s*$",
    re.IGNORECASE,
)

# The arXiv API returns Atom XML. The first <title> is the feed title
# ("arXiv Query: ..."), and the second is the paper title — we want the
# second. ``re.DOTALL`` lets us span the whitespace between elements.
_ARXIV_TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.DOTALL)


def parse_arxiv_doi(doi: str | None) -> str | None:
    """Return arXiv id (e.g. ``2005.11401``) or None if not an arXiv DOI."""
    if not doi:
        return None
    m = _ARXIV_DOI_RE.match(doi)
    return m.group(1) if m else None


async def resolve_arxiv_title(
    arxiv_id: str, client: httpx.AsyncClient,
) -> str | None:
    """Resolve an arXiv id to its paper title via the arXiv Atom API.

    Returns ``None`` on network error, malformed response, or when fewer
    than two <title> elements are present (the first is the feed
    header). Tolerates the ``vN`` version suffix transparently —
    arXiv's API ignores it.
    """
    if not arxiv_id:
        return None
    url = "https://export.arxiv.org/api/query"
    try:
        resp = await client.get(
            url, params={"id_list": arxiv_id}, timeout=10.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("arxiv_title_resolve_error", arxiv_id=arxiv_id, error=str(exc))
        return None
    if resp.status_code != 200:
        logger.info(
            "arxiv_title_resolve_miss", arxiv_id=arxiv_id, status=resp.status_code,
        )
        return None
    titles = _ARXIV_TITLE_RE.findall(resp.text or "")
    if len(titles) < 2:
        return None
    # The arXiv API wraps titles across lines with leading whitespace;
    # normalise to a single line so downstream title.search works.
    raw = titles[1].strip()
    return re.sub(r"\s+", " ", raw)
