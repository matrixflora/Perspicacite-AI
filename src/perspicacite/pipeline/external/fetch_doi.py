"""Synced from AgenticScienceBuilder @ a10eced — httpx-adapted, keep API in sync.

DOI-keyed lookup helpers: Crossref (citation metadata), Unpaywall (open-access
PDF URL), PubMed (NCBI eutils — abstract via PMID), PMCID lookup for DOI.

Each helper caches via the cache layer (Task 2). On any error, returns None
without raising. Writes a small JSON artifact under the capsule's external/
subtree so the result is available offline after the fetch.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.external.http import http_get_json, http_get_text

logger = get_logger("perspicacite.external.doi")


def _doi_slug(doi: str) -> str:
    """Filesystem-safe slug for a DOI (e.g., ``10.1234_abc.def``)."""
    return doi.replace("/", "_").replace(":", "_")


async def fetch_crossref(
    doi: str, *,
    capsule_dir: Path,
    cache_dir: Path,
    ttl_seconds: int = 30 * 86400,
) -> dict[str, Any] | None:
    """Fetch Crossref metadata for ``doi`` and write to ``capsule/external/crossref/<slug>.json``."""
    url = f"https://api.crossref.org/works/{doi}"
    data = await http_get_json(
        url, cache_dir=cache_dir, api="crossref", query=doi,
        ttl_seconds=ttl_seconds,
        headers={"User-Agent": "perspicacite/0.1 (https://github.com/HolobiomicsLab/Perspicacite-AI; mailto:louisfelix.nothias@gmail.com)"},
    )
    if data is None:
        return None
    out_dir = capsule_dir / "external" / "crossref"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{_doi_slug(doi)}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8",
    )
    return data


async def fetch_unpaywall(
    doi: str, *,
    capsule_dir: Path,
    cache_dir: Path,
    email: str | None = None,
    ttl_seconds: int = 30 * 86400,
) -> dict[str, Any] | None:
    """Fetch Unpaywall record for ``doi`` (open-access URL discovery).

    Unpaywall requires an email in the query string. Pass via ``email``;
    fall back to a sentinel ``contact@perspicacite.example`` if not set —
    callers should provide a real email in production.
    """
    contact = email or "contact@perspicacite.example"
    url = f"https://api.unpaywall.org/v2/{doi}?email={contact}"
    data = await http_get_json(
        url, cache_dir=cache_dir, api="unpaywall", query=doi,
        ttl_seconds=ttl_seconds,
    )
    if data is None:
        return None
    out_dir = capsule_dir / "external" / "unpaywall"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{_doi_slug(doi)}.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8",
    )
    return data


def _parse_pubmed_abstract(xml_text: str) -> str | None:
    """Parse the ``AbstractText`` nodes out of an eutils ``efetch`` XML response."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    chunks: list[str] = []
    for node in root.iter("AbstractText"):
        label = node.attrib.get("Label")
        text = "".join(node.itertext()).strip()
        if not text:
            continue
        chunks.append(f"{label}: {text}" if label else text)
    return "\n\n".join(chunks) if chunks else None


async def fetch_pubmed(
    pmid: str, *,
    capsule_dir: Path,
    cache_dir: Path,
    ttl_seconds: int = 30 * 86400,
) -> dict[str, Any] | None:
    """Fetch PubMed abstract + metadata by PMID. Writes to
    ``capsule/external/pubmed/<pmid>.json``.

    Returns ``{"pmid": str, "abstract": str | None, "raw_xml_path": str}``."""
    url = (
        "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
        f"?db=pubmed&id={pmid}&retmode=xml"
    )
    xml_text = await http_get_text(
        url, cache_dir=cache_dir, api="pubmed", query=pmid,
        ttl_seconds=ttl_seconds,
    )
    if xml_text is None:
        return None
    out_dir = capsule_dir / "external" / "pubmed"
    out_dir.mkdir(parents=True, exist_ok=True)
    xml_path = out_dir / f"{pmid}.xml"
    xml_path.write_text(xml_text, encoding="utf-8")
    abstract = _parse_pubmed_abstract(xml_text)
    record: dict[str, Any] = {
        "pmid": pmid,
        "abstract": abstract,
        "raw_xml_path": str(xml_path),
    }
    (out_dir / f"{pmid}.json").write_text(
        json.dumps(record, indent=2), encoding="utf-8",
    )
    return record


_PMCID_RE = re.compile(r"PMC\d+")


async def fetch_pmcid_for_doi(
    doi: str, *,
    cache_dir: Path,
    ttl_seconds: int = 30 * 86400,
) -> str | None:
    """Resolve a DOI to a PMCID via NCBI's ID-Converter API. Returns PMCID
    string (e.g., ``"PMC1234567"``) or None."""
    url = (
        "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
        f"?ids={doi}&format=json&tool=perspicacite&email=contact@perspicacite.example"
    )
    data = await http_get_json(
        url, cache_dir=cache_dir, api="pmcid_for_doi", query=doi,
        ttl_seconds=ttl_seconds,
    )
    if not data or not isinstance(data, dict):
        return None
    records = data.get("records") or []
    for r in records:
        pmcid = r.get("pmcid")
        if pmcid and _PMCID_RE.search(pmcid):
            return pmcid
    return None
