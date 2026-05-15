from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.snowball import _fetch_seed_work


@pytest.mark.asyncio
async def test_fetch_seed_work_falls_back_to_arxiv_id_on_doi_404(monkeypatch):
    """For arXiv DOIs, when /works/doi:... 404s, _fetch_seed_work must
    retry via the ids.arxiv filter — same fallback as openalex_id_for_doi.
    """
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append({"url": url, "params": dict(kwargs.get("params") or {})})
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        # Fallback path: list endpoint with ids.arxiv filter.
        assert kwargs.get("params", {}).get("filter", "").startswith("ids.arxiv:")
        return httpx.Response(
            200,
            json={"results": [{"id": "https://openalex.org/W3098425262",
                                "display_name": "RAG"}]},
            request=req,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.2005.11401", {})
    assert work is not None
    assert work["id"] == "https://openalex.org/W3098425262"
    # Two HTTP calls: doi miss then arxiv filter hit.
    assert len(calls) == 2
    assert calls[0]["url"].endswith("doi:10.48550/arXiv.2005.11401")
    assert calls[1]["params"]["filter"] == "ids.arxiv:2005.11401"


@pytest.mark.asyncio
async def test_fetch_seed_work_returns_none_when_arxiv_fallback_also_misses(monkeypatch):
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.9999.99999", {})
    assert work is None


@pytest.mark.asyncio
async def test_fetch_seed_work_non_arxiv_doi_returns_none_on_404(monkeypatch):
    """Non-arXiv DOIs should not trigger the fallback — return None as before."""
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append(url)
        req = httpx.Request("GET", url)
        return httpx.Response(404, json={}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.1234/not-arxiv", {})
    assert work is None
    # Only one call — fallback was not triggered.
    assert len(calls) == 1
