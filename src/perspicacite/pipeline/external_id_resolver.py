"""arXiv → DOI and PMC → DOI resolvers.

Task-5 follow-up for the 2026-05-15 GitHub skill-bundle ingest pipeline.
``ingest_skill_bundle`` only auto-routes DOIs through
:func:`perspicacite.pipeline.search_to_kb.ingest_dois_into_kb`; arXiv +
PMC ids previously fell into ``linked_papers_skipped_non_doi``. This
module lets the orchestrator translate those identifiers into DOIs
upstream so the existing DOI ingest path can take them.

Both resolvers return ``None`` on any failure (network error, missing
upstream metadata, no DOI in response). They NEVER raise — the
orchestrator must always be able to fall back to "skip this ref" when
resolution fails.

External APIs used:
  * arXiv Atom API — title lookup, via
    :func:`perspicacite.pipeline.arxiv_ids.resolve_arxiv_title`.
  * OpenAlex ``/works/doi:<doi>`` — short-circuit for arXiv-native DOIs
    (some preprints ship a publisher-side DOI of the form
    ``10.48550/arxiv.<id>`` that OpenAlex stores directly).
  * OpenAlex ``/works?filter=title.search:"<title>"&per_page=1`` —
    title-based DOI lookup fallback.
  * NCBI PMC ID converter at
    ``/pmc/utils/idconv/v1.0/?ids=<pmc>&format=json`` — returns a
    ``records[*].doi`` field when present.
"""
from __future__ import annotations

import httpx

from perspicacite.logging import get_logger
from perspicacite.pipeline.arxiv_ids import resolve_arxiv_title
from perspicacite.pipeline.github.bundle import _normalize_doi

logger = get_logger("perspicacite.pipeline.external_id_resolver")

#: NCBI requests a contact User-Agent for unauthenticated traffic. Per
#: their documented etiquette, use a static value with the project name
#: and major version — easier for them to throttle if we misbehave.
_USER_AGENT = "Perspicacite/2.0"

#: Standard timeout for both arXiv + OpenAlex + NCBI calls. The other
#: HTTP wrappers in this codebase use 20–30s; for the linked-paper
#: resolver path we want a tighter budget because we'll be calling it
#: once per arXiv/PMC ref in a bundle (potentially dozens).
_TIMEOUT_SECS = 10.0

_OPENALEX_BASE = "https://api.openalex.org"
_NCBI_IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"

#: Common URL prefix on the OpenAlex ``Work.doi`` field. Strip both
#: ``http://`` and ``https://`` forms (and the ``dx.`` historical
#: variant) to recover the bare DOI.
import re as _re

_DOI_URL_PREFIX = _re.compile(r"^https?://(?:dx\.)?doi\.org/", _re.IGNORECASE)


def _extract_doi_from_openalex_work(work: dict) -> str | None:
    """Pull the ``doi`` field off an OpenAlex Work record and normalise.

    Returns the bare DOI (``10.x/y``) — strips the
    ``https://doi.org/`` URL prefix that OpenAlex prepends. ``None`` if
    the field is missing or empty.
    """
    if not isinstance(work, dict):
        return None
    raw = work.get("doi")
    if not isinstance(raw, str) or not raw.strip():
        return None
    bare = _DOI_URL_PREFIX.sub("", raw.strip())
    if not bare:
        return None
    return _normalize_doi(bare)


async def resolve_arxiv_to_doi(
    arxiv_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Resolve an arXiv id (e.g. ``2204.12345``) to a publisher DOI.

    Short-circuits via OpenAlex ``/works/doi:10.48550/arxiv.<id>`` when
    that record exists (some preprints ship a publisher-side DOI of the
    form ``10.48550/arxiv.<id>`` that OpenAlex stores directly). If
    that misses, falls back to:

      1. arXiv Atom API → paper title
         (:func:`perspicacite.pipeline.arxiv_ids.resolve_arxiv_title`).
      2. OpenAlex ``/works?filter=title.search:"<title>"&per_page=1`` →
         Work record → ``doi`` field.

    Returns the bare DOI (prefix lowercased per the standard, suffix
    preserved) or ``None`` if any step fails. Never raises.

    Parameters
    ----------
    arxiv_id : str
        Bare arXiv id (e.g. ``"2204.12345"`` or ``"2204.12345v2"``). The
        ``vN`` version suffix is tolerated transparently.
    client : httpx.AsyncClient, optional
        Reuse an existing client for connection pooling. Defaults to a
        one-off client per call — matches the pattern in
        :mod:`perspicacite.pipeline.arxiv_ids`.
    """
    if not arxiv_id:
        return None

    if client is None:
        async with httpx.AsyncClient() as owned_client:
            return await _resolve_arxiv_to_doi_inner(arxiv_id, owned_client)
    return await _resolve_arxiv_to_doi_inner(arxiv_id, client)


async def _resolve_arxiv_to_doi_inner(
    arxiv_id: str, client: httpx.AsyncClient,
) -> str | None:
    headers = {"User-Agent": _USER_AGENT}

    # Step 1: short-circuit on arXiv-native DOI in OpenAlex. Some
    # preprints (Nature Physics etc.) ship a publisher-side DOI of the
    # form ``10.48550/arxiv.<id>``; OpenAlex caches them as Works.
    native_doi = f"10.48550/arxiv.{arxiv_id}"
    short_circuit_url = f"{_OPENALEX_BASE}/works/doi:{native_doi}"
    try:
        resp = await client.get(
            short_circuit_url, headers=headers, timeout=_TIMEOUT_SECS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "external_id_resolver.arxiv_short_circuit_error",
            arxiv_id=arxiv_id, error=str(exc),
        )
        # Fall through to the title-based chain on transient errors.
        resp = None

    if resp is not None and resp.status_code == 200:
        work = resp.json() or {}
        doi = _extract_doi_from_openalex_work(work)
        if doi:
            return doi
        # 200 but no DOI on the record: keep going.

    # Step 2: title lookup via arXiv API.
    title = await resolve_arxiv_title(arxiv_id, client)
    if not title:
        return None

    # Step 3: OpenAlex title.search → first hit's DOI.
    title_filter = f'title.search:"{title}"'
    try:
        resp = await client.get(
            f"{_OPENALEX_BASE}/works",
            params={"filter": title_filter, "per_page": 1},
            headers=headers,
            timeout=_TIMEOUT_SECS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "external_id_resolver.arxiv_title_search_error",
            arxiv_id=arxiv_id, error=str(exc),
        )
        return None

    if resp.status_code != 200:
        logger.info(
            "external_id_resolver.arxiv_title_search_miss",
            arxiv_id=arxiv_id, status=resp.status_code,
        )
        return None

    payload = resp.json() or {}
    results = payload.get("results") or []
    if not results:
        return None
    return _extract_doi_from_openalex_work(results[0])


async def resolve_pmc_to_doi(
    pmc_id: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Resolve a PMC id (e.g. ``PMC9123456``) to a DOI.

    Uses NCBI's `PMC ID Converter`_::

        GET https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?ids=<pmc>&format=json

    The response shape is::

        {"records": [{"pmcid": "PMC9123456", "doi": "10.x/y", ...}]}

    Returns the normalised DOI (prefix lowercased) or ``None`` if the
    converter returns no DOI for the id. Never raises.

    .. _PMC ID Converter:
       https://www.ncbi.nlm.nih.gov/pmc/tools/id-converter-api/
    """
    if not pmc_id:
        return None

    if client is None:
        async with httpx.AsyncClient() as owned_client:
            return await _resolve_pmc_to_doi_inner(pmc_id, owned_client)
    return await _resolve_pmc_to_doi_inner(pmc_id, client)


async def _resolve_pmc_to_doi_inner(
    pmc_id: str, client: httpx.AsyncClient,
) -> str | None:
    headers = {"User-Agent": _USER_AGENT}
    try:
        resp = await client.get(
            _NCBI_IDCONV_URL,
            params={"ids": pmc_id, "format": "json"},
            headers=headers,
            timeout=_TIMEOUT_SECS,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "external_id_resolver.pmc_idconv_error",
            pmc_id=pmc_id, error=str(exc),
        )
        return None

    if resp.status_code != 200:
        logger.info(
            "external_id_resolver.pmc_idconv_miss",
            pmc_id=pmc_id, status=resp.status_code,
        )
        return None

    payload = resp.json() or {}
    records = payload.get("records") or []
    if not records:
        return None
    first = records[0]
    if not isinstance(first, dict):
        return None
    raw_doi = first.get("doi")
    if not isinstance(raw_doi, str) or not raw_doi.strip():
        return None
    return _normalize_doi(raw_doi.strip())


__all__ = [
    "resolve_arxiv_to_doi",
    "resolve_pmc_to_doi",
]
