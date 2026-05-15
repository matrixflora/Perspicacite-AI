from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.arxiv_ids import parse_arxiv_doi
from perspicacite.pipeline.snowball import openalex_id_for_doi


def test_parse_arxiv_doi_extracts_id():
    assert parse_arxiv_doi("10.48550/arXiv.2005.11401") == "2005.11401"
    assert parse_arxiv_doi("10.48550/arxiv.2305.12345v2") == "2305.12345v2"
    assert parse_arxiv_doi("10.1038/nature12373") is None
    assert parse_arxiv_doi("") is None
    assert parse_arxiv_doi(None) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_openalex_id_for_doi_arxiv_fallback(monkeypatch):
    """When /works/doi:... 404s for an arXiv DOI, retry via arxiv_id filter."""
    calls: list[str] = []

    async def fake_get(self, url, **kwargs):
        calls.append(str(url) + "?" + repr(kwargs.get("params") or {}))
        req = httpx.Request("GET", url)
        # First call: /works/doi:10.48550/arXiv.2005.11401 -> 404
        if "doi:" in str(url):
            return httpx.Response(404, json={}, request=req)
        # Second call: /works?filter=ids.arxiv:2005.11401 -> 1 hit
        params = kwargs.get("params") or {}
        assert params.get("filter") == "ids.arxiv:2005.11401"
        return httpx.Response(
            200,
            json={"results": [{"id": "https://openalex.org/W3098425262"}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(
            client, "10.48550/arXiv.2005.11401", headers={},
        )
    assert oa_id == "W3098425262"
    assert len(calls) == 2  # primary + fallback
