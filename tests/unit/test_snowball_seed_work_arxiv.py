from __future__ import annotations

import httpx
import pytest

from perspicacite.pipeline.snowball import _fetch_seed_work

# Minimal arXiv Atom XML used to fake the title-resolution call.
_ARXIV_ATOM_RAG = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>arXiv Query: search_query=&amp;id_list=2005.11401</title>
  <entry>
    <title>Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks</title>
  </entry>
</feed>
"""


@pytest.mark.asyncio
async def test_fetch_seed_work_falls_back_to_arxiv_title_search_on_doi_404(monkeypatch):
    """For arXiv DOIs, when /works/doi:... 404s, _fetch_seed_work must
    resolve the arXiv id to a title via export.arxiv.org and then query
    OpenAlex filter=title.search.

    The original fallback used filter=ids.arxiv:<id>, which OpenAlex
    returns HTTP 400 on (no such filter). Audit 2026-05-15 re-run
    discovered the bug; this test pins the working chain.
    """
    calls = []

    async def fake_get(self, url, **kwargs):
        calls.append({"url": url, "params": dict(kwargs.get("params") or {})})
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=_ARXIV_ATOM_RAG, request=req)
        # OpenAlex title.search fallback.
        f = kwargs.get("params", {}).get("filter", "")
        assert f.startswith('title.search:"'), f
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
    # Three HTTP calls: openalex doi miss → arxiv API title → openalex title.search hit
    assert len(calls) == 3
    assert calls[0]["url"].endswith("doi:10.48550/arXiv.2005.11401")
    assert "export.arxiv.org" in calls[1]["url"]
    assert calls[2]["params"]["filter"] == 'title.search:"Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"'


@pytest.mark.asyncio
async def test_fetch_seed_work_returns_none_when_arxiv_api_misses(monkeypatch):
    """If the arXiv API has no record, the chain returns None — we don't
    fall through to a bogus OpenAlex query."""
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        if "export.arxiv.org" in url:
            # Atom feed with only the feed-header title (no entry).
            return httpx.Response(
                200,
                text='<feed><title>arXiv Query: x</title></feed>',
                request=req,
            )
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.9999.99999", {})
    assert work is None


@pytest.mark.asyncio
async def test_fetch_seed_work_returns_none_when_title_search_also_misses(monkeypatch):
    """When arxiv title is known but OpenAlex title.search returns no
    results, the chain returns None."""
    async def fake_get(self, url, **kwargs):
        req = httpx.Request("GET", url)
        if "/works/doi:" in url:
            return httpx.Response(404, json={}, request=req)
        if "export.arxiv.org" in url:
            return httpx.Response(200, text=_ARXIV_ATOM_RAG, request=req)
        return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    async with httpx.AsyncClient() as client:
        work = await _fetch_seed_work(client, "10.48550/arXiv.2005.11401", {})
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
    # Only one call — neither arxiv API nor title.search was tried.
    assert len(calls) == 1
