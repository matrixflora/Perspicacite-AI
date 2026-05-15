"""Unit tests for the Semantic Scholar references/citations fetchers.

These back the SS fallback path in snowball_expand. The adapter
(_ss_record_to_oa_like_work) maps S2's nested {citedPaper: {...}}
shape to the OpenAlex-like dict that _paper_from_oa_work consumes,
so downstream ExpansionHit construction is uniform.
"""
from __future__ import annotations

import httpx
import pytest

from perspicacite.search.semantic_scholar import (
    fetch_ss_references,
    fetch_ss_citations,
)


_SAMPLE_REF_RESPONSE = {
    "data": [
        {
            "isInfluential": True,
            "citedPaper": {
                "paperId": "ssid-cited-1",
                "corpusId": 42,
                "externalIds": {"DOI": "10.1234/cited", "ArXiv": "1234.5678"},
                "title": "A Cited Work",
                "abstract": "abstract text",
                "authors": [{"name": "Ada Lovelace"}],
                "year": 2020,
                "citationCount": 99,
                "venue": "NeurIPS",
            },
        }
    ]
}

_SAMPLE_CIT_RESPONSE = {
    "data": [
        {
            "isInfluential": False,
            "citingPaper": {
                "paperId": "ssid-citing-1",
                "externalIds": {"DOI": "10.1234/citer"},
                "title": "A Citing Work",
                "abstract": "abstract",
                "authors": [{"name": "Babbage"}],
                "year": 2023,
                "citationCount": 5,
                "venue": "ICML",
            },
        }
    ]
}


@pytest.mark.asyncio
async def test_fetch_ss_references_happy_path(monkeypatch):
    async def fake_get(self, url, **kwargs):
        assert "/references" in url
        return httpx.Response(200, json=_SAMPLE_REF_RESPONSE, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.2005.11401", limit=5)
    assert len(out) == 1
    rec = out[0]
    # Adapter must produce OpenAlex-like keys consumed by _paper_from_oa_work:
    assert rec["doi"] == "https://doi.org/10.1234/cited"
    assert rec["title"] == "A Cited Work"
    assert rec["publication_year"] == 2020
    assert rec["cited_by_count"] == 99
    # _paper_from_oa_work reads journal from primary_location.source.display_name:
    assert rec["primary_location"]["source"]["display_name"] == "NeurIPS"
    # Authors flattened into OpenAlex's authorships shape:
    assert any("Ada" in (a.get("author") or {}).get("display_name", "") for a in rec["authorships"])
    # Preserve the arXiv id and S2 identifiers for diagnostic / future dedup use:
    assert rec.get("metadata", {}).get("arxiv_id") == "1234.5678"
    assert rec.get("metadata", {}).get("s2_paper_id") == "ssid-cited-1"


@pytest.mark.asyncio
async def test_fetch_ss_citations_happy_path(monkeypatch):
    async def fake_get(self, url, **kwargs):
        assert "/citations" in url
        return httpx.Response(200, json=_SAMPLE_CIT_RESPONSE, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_citations("DOI:10.48550/arXiv.2005.11401", limit=5)
    assert len(out) == 1
    assert out[0]["doi"] == "https://doi.org/10.1234/citer"
    assert out[0]["title"] == "A Citing Work"


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_404(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(404, json={}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.notfound")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_429(monkeypatch):
    async def fake_get(self, url, **kwargs):
        return httpx.Response(429, json={}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.X")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_references_handles_network_error(monkeypatch):
    async def fake_get(self, url, **kwargs):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    out = await fetch_ss_references("DOI:10.48550/arXiv.X")
    assert out == []


@pytest.mark.asyncio
async def test_fetch_ss_citations_clamps_limit(monkeypatch):
    """Limit must be clamped to [1, 1000] before being sent to S2."""
    captured: dict = {}
    async def fake_get(self, url, **kwargs):
        captured["params"] = kwargs.get("params") or {}
        return httpx.Response(200, json={"data": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    await fetch_ss_citations("DOI:test", limit=5000)
    assert captured["params"]["limit"] == 1000

    captured.clear()
    await fetch_ss_citations("DOI:test", limit=0)
    assert captured["params"]["limit"] == 1
