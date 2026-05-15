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
    """When /works/doi:... 404s for an arXiv DOI, retry via arXiv title-search chain.

    Production flow (9ad0baa):
      1. GET api.openalex.org/works/doi:... → 404
      2. GET export.arxiv.org/api/query?id_list=<arxiv_id> → Atom XML with title
      3. GET api.openalex.org/works?filter=title.search:"<title>" → 1 hit
    """
    calls: list[str] = []

    ARXIV_ATOM = (
        '<?xml version="1.0"?><feed>'
        '<title>arXiv Query</title>'
        '<entry><title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>'
        '</entry></feed>'
    )

    async def fake_get(self, url, **kwargs):
        calls.append(str(url) + "?" + repr(kwargs.get("params") or {}))
        req = httpx.Request("GET", url)
        # Call 1: OpenAlex primary DOI lookup -> 404
        if "doi:" in str(url):
            return httpx.Response(404, json={}, request=req)
        # Call 2: arXiv Atom API -> returns XML with title
        if "export.arxiv.org" in str(url):
            params = kwargs.get("params") or {}
            assert params.get("id_list") == "2005.11401"
            return httpx.Response(200, text=ARXIV_ATOM, request=req)
        # Call 3: OpenAlex title.search -> 1 hit
        params = kwargs.get("params") or {}
        assert "title.search" in (params.get("filter") or "")
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
    assert len(calls) == 3  # primary DOI + arXiv title + OpenAlex title.search
