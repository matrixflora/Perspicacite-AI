"""OpenCitations COCI citation fetcher."""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.opencitations")

_COCI_BASE = "https://opencitations.net/index/coci/api/v1/citations"


async def fetch_opencitations_citations(
    doi: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Fetch papers citing ``doi`` from OpenCitations COCI.

    Returns a list of OpenAlex-like work dicts with keys:
    ``doi``, ``publication_year``, ``id`` (synthetic).
    Returns [] on any error (404, network, parse failure).
    """
    if not doi:
        return []

    client = http_client or httpx.AsyncClient(timeout=20.0)
    should_close = http_client is None

    try:
        url = f"{_COCI_BASE}/{doi}"
        resp = await client.get(url, headers={"Accept": "application/json"})

        if resp.status_code == 404:
            return []
        if resp.status_code >= 400:
            logger.warning("coci_http_error", doi=doi, status=resp.status_code)
            return []

        records = resp.json()
        if not isinstance(records, list):
            return []

        results: list[dict[str, Any]] = []
        for rec in records:
            citing_doi = rec.get("citing") or ""
            if not citing_doi:
                continue

            year: int | None = None
            creation = rec.get("creation") or ""
            if creation and len(creation) >= 4:
                with contextlib.suppress(ValueError):
                    year = int(creation[:4])

            results.append({
                "doi": citing_doi,
                "publication_year": year,
                "id": f"https://doi.org/{citing_doi}",
                "title": "",
                "display_name": "",
                "cited_by_count": 0,
                "abstract_inverted_index": None,
                "authorships": [],
                "primary_location": {},
                "metadata": {
                    "coci_oci": rec.get("oci"),
                    "coci_timespan": rec.get("timespan"),
                },
            })

        logger.info("coci_fetch", doi=doi, citing_count=len(results))
        return results

    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("coci_fetch_error", doi=doi, error=str(exc))
        return []

    finally:
        if should_close:
            await client.aclose()
