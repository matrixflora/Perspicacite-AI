"""PubChem compound → PubMed literature search provider."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

from perspicacite.logging import get_logger

if TYPE_CHECKING:
    from perspicacite.models.papers import Paper

logger = get_logger("perspicacite.search.pubchem")

_PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug"
_INCHIKEY_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
# Characters that appear in SMILES but not in plain names
_SMILES_CHARS = set("=#()[]@+-/\\%")
# SMILES ring-closure: atom letter immediately followed by a digit (e.g. C1, N2)
_SMILES_RING_RE = re.compile(r"[A-Za-z]\d")


def _detect_input_type(query: str) -> str:
    """Classify query as 'inchikey', 'smiles', or 'name'."""
    if _INCHIKEY_RE.match(query.strip()):
        return "inchikey"
    if any(c in query for c in _SMILES_CHARS):
        return "smiles"
    # Ring-closure notation (e.g. C1CCCCC1, CC(=O)... already caught above)
    if _SMILES_RING_RE.search(query):
        return "smiles"
    return "name"


async def _get_cid(input_value: str, input_type: str, client: httpx.AsyncClient) -> int | None:
    url = f"{_PUBCHEM_BASE}/compound/{input_type}/{input_value}/cids/JSON"
    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        cids = resp.json().get("IdentifierList", {}).get("CID", [])
        return cids[0] if cids else None
    except Exception as exc:
        logger.warning("pubchem_cid_lookup_error", input_type=input_type, error=str(exc))
        return None


async def _get_pmids_for_cid(cid: int, client: httpx.AsyncClient) -> list[int]:
    url = f"{_PUBCHEM_BASE}/compound/cid/{cid}/xrefs/PubMedID/JSON"
    try:
        resp = await client.get(url, timeout=15.0)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        info_list = resp.json().get("InformationList", {}).get("Information", [])
        if not info_list:
            return []
        return [int(p) for p in info_list[0].get("PubMedID", [])]
    except Exception as exc:
        logger.warning("pubchem_pmid_lookup_error", cid=cid, error=str(exc))
        return []


async def _pmids_to_papers(
    pmids: list[int],
    email: str | None,
    max_results: int,
) -> list[Paper]:
    if not pmids:
        return []
    try:
        from perspicacite.search.pubmed import PubMedSearchAdapter

        _placeholders = {
            "",
            "user@example.com",
            "you@example.com",
            "your.email@domain.com",
            "email@example.com",
            "test@test.com",
        }
        eff_email = (
            email
            if email and email.strip().lower() not in _placeholders
            else "pubchem@perspicacite.local"
        )
        adapter = PubMedSearchAdapter(email=eff_email)
        pmid_query = " OR ".join(f"{p}[pmid]" for p in pmids[:max_results])
        return await adapter.search(pmid_query, max_results=max_results)
    except Exception as exc:
        logger.warning("pubchem_pmids_to_papers_error", error=str(exc))
        return []


class PubChemSearchProvider:
    """Finds papers by compound name / InChIKey / SMILES via PubChem literature API."""

    name = "pubchem"
    description = "PubChem compound → PubMed literature search (two-hop: CID → PMIDs → Papers)"
    domains: ClassVar[list[str]] = ["chemistry"]
    tier: str = "external"
    retry: int = 1

    def __init__(self, ncbi_email: str | None = None) -> None:
        self._email = ncbi_email

    async def search(
        self,
        query: str,
        max_results: int = 20,
        year_min: int | None = None,
        year_max: int | None = None,
        **_: Any,
    ) -> list[Paper]:
        input_type = _detect_input_type(query.strip())

        async with httpx.AsyncClient(timeout=20.0) as client:
            cid = await _get_cid(query.strip(), input_type, client)
            if cid is None:
                logger.info("pubchem_no_cid", query=query[:80])
                return []
            pmids = await _get_pmids_for_cid(cid, client)

        if not pmids:
            logger.info("pubchem_no_pmids", cid=cid, query=query[:80])
            return []

        papers = await _pmids_to_papers(pmids, self._email, max_results)
        logger.info(
            "pubchem_search",
            query=query[:80],
            cid=cid,
            pmids=len(pmids),
            papers=len(papers),
        )
        return papers
