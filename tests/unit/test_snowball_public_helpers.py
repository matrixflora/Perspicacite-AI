from __future__ import annotations
import pytest
import httpx

from perspicacite.pipeline.snowball import (
    openalex_id_for_doi,
    fetch_cited_by_works,
)


@pytest.mark.asyncio
async def test_openalex_id_for_doi_uses_works_doi_endpoint(monkeypatch):
    captured = {}

    async def fake_get(self, url, **kwargs):
        captured["url"] = url
        req = httpx.Request("GET", url)
        return httpx.Response(
            200, json={"id": "https://openalex.org/W1234567890"}, request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(client, "10.1000/test", headers={})
    assert oa_id == "W1234567890"
    assert "doi:10.1000/test" in captured["url"]


@pytest.mark.asyncio
async def test_openalex_id_for_doi_returns_none_on_miss(monkeypatch):
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(404, json={}, request=req)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        oa_id = await openalex_id_for_doi(client, "10.1000/missing", headers={})
    assert oa_id is None


@pytest.mark.asyncio
async def test_fetch_cited_by_works_returns_works(monkeypatch):
    seed_work = {"cited_by_api_url": "https://api.openalex.org/works?filter=cites:W1"}
    page = {
        "results": [{"id": f"https://openalex.org/W{i}"} for i in range(10, 20)],
        "meta": {"next_cursor": None},
    }

    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        return httpx.Response(200, json=page, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with httpx.AsyncClient() as client:
        works = await fetch_cited_by_works(
            client, seed_work=seed_work, max_results=15, headers={},
        )
    assert len(works) == 10  # one page of 10 results
