"""Crossref REST API metadata enrichment helper.

Queries ``GET https://api.crossref.org/works/{doi}`` and returns a *patch*
dict containing only the fields that are missing/empty in ``base_metadata``.
An existing value is never overwritten.  Any network or parse failure returns
an empty dict without raising.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.download.crossref")

_CROSSREF_BASE = "https://api.crossref.org/works"


def _normalize_doi(doi: str) -> str:
    """Strip doi.org URL prefixes, returning the bare DOI string."""
    doi = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
    return doi


def _strip_jats(text: str) -> str:
    """Remove XML/JATS tags from *text*, returning the plain-text content."""
    return re.sub(r"<[^>]+>", "", text).strip()


async def enrich_from_crossref(
    doi: str,
    *,
    http_client: httpx.AsyncClient,
    base_metadata: dict[str, Any],
    mailto: str | None = None,
) -> dict[str, Any]:
    """Query Crossref and return a patch dict for fields missing in *base_metadata*.

    Parameters
    ----------
    doi:
        The paper DOI (with or without ``https://doi.org/`` prefix).
    http_client:
        A shared :class:`httpx.AsyncClient` instance.
    base_metadata:
        The metadata dict already populated by earlier discovery steps.
        Only keys whose value is ``None``, ``""``, or ``[]`` (or absent) will
        be candidates for enrichment.
    mailto:
        Optional e-mail address for the Crossref polite pool.  When supplied,
        it is included in the ``User-Agent`` header for better rate limits.

    Returns
    -------
    dict[str, Any]
        A patch dict containing only the newly resolved fields.  Empty dict on
        any failure.
    """
    bare_doi = _normalize_doi(doi)

    headers: dict[str, str] = {}
    if mailto:
        headers["User-Agent"] = f"perspicacite/2 (mailto:{mailto})"

    def _missing(key: str) -> bool:
        val = base_metadata.get(key)
        return val is None or val == "" or val == []

    try:
        resp = await http_client.get(
            f"{_CROSSREF_BASE}/{bare_doi}",
            headers=headers,
            timeout=20.0,
        )
        resp.raise_for_status()
        msg: dict[str, Any] = resp.json().get("message") or {}
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "crossref_http_error",
            doi=doi,
            status_code=exc.response.status_code,
        )
        return {}
    except Exception as exc:
        logger.warning("crossref_request_failed", doi=doi, error=str(exc))
        return {}

    patch: dict[str, Any] = {}

    # Title
    if _missing("title") and msg.get("title"):
        patch["title"] = msg["title"][0]

    # Journal / container-title
    if _missing("journal") and msg.get("container-title"):
        patch["journal"] = msg["container-title"][0]

    # Year — prefer "published", fall back to "issued"
    if _missing("year"):
        date_info = msg.get("published") or msg.get("issued") or {}
        date_parts = date_info.get("date-parts")
        if date_parts and date_parts[0]:
            patch["year"] = date_parts[0][0]

    # Authors
    if _missing("authors"):
        raw_authors = msg.get("author") or []
        names = [f"{a.get('given', '')} {a.get('family', '')}".strip() for a in raw_authors]
        names = [n for n in names if n]
        if names:
            patch["authors"] = names

    # Abstract (strip JATS XML tags)
    if _missing("abstract"):
        raw_abstract = msg.get("abstract")
        if raw_abstract:
            stripped = _strip_jats(raw_abstract)
            if stripped:
                patch["abstract"] = stripped

    # References
    if _missing("references") and msg.get("reference"):
        refs = [
            {
                "doi": r.get("DOI"),
                "title": r.get("article-title") or r.get("unstructured"),
                "year": r.get("year"),
            }
            for r in msg["reference"]
        ]
        if refs:
            patch["references"] = refs

    # License
    if _missing("license"):
        for lic in msg.get("license") or []:
            url = lic.get("URL")
            if url:
                patch["license"] = url
                break

    if patch:
        logger.info("crossref_enriched", doi=doi, fields=sorted(patch.keys()))

    return patch
