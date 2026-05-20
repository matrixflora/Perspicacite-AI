"""Unit tests for the SerpApi Google Scholar provider + fallback chain.

Mocked — no network, no SerpApi quota used.
"""

import pytest

from perspicacite.search import serpapi_scholar
from perspicacite.search.serpapi_scholar import (
    GoogleScholarChainProvider,
    SerpApiScholarProvider,
)

SAMPLE = {
    "organic_results": [
        {
            "title": "AgentBench: Evaluating LLMs as Agents",
            "link": "https://arxiv.org/abs/2308.03688",
            "snippet": "We present AgentBench, a multi-dimensional benchmark...",
            "publication_info": {
                "summary": "X Liu, H Yu, H Zhang - arXiv preprint, 2023 - arxiv.org",
                "authors": [{"name": "X Liu"}, {"name": "H Yu"}],
            },
            "inline_links": {"cited_by": {"total": 684}},
            "resources": [
                {"file_format": "PDF", "link": "https://arxiv.org/pdf/2308.03688.pdf"}
            ],
        },
        {
            # No structured authors list → parse from summary; no PDF resource.
            "title": "alpha-Rank: Multi-Agent Evaluation by Evolution",
            "link": "https://www.nature.com/articles/s41598-019-45619-9",
            "snippet": "We introduce alpha-Rank...",
            "publication_info": {
                "summary": "S Omidshafiei, C Papadimitriou - Scientific reports, 2019 - nature.com",
            },
            "inline_links": {"cited_by": {"total": 185}},
        },
    ]
}


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, data):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        return _FakeResp(self._data)


def _patch_httpx(monkeypatch, data):
    monkeypatch.setattr(
        serpapi_scholar.httpx, "AsyncClient", lambda **kw: _FakeClient(data)
    )


@pytest.mark.asyncio
async def test_serpapi_parses_results(monkeypatch):
    _patch_httpx(monkeypatch, SAMPLE)
    p = SerpApiScholarProvider(api_key="test-key")
    papers = await p.search("agent evaluation", max_results=10)
    assert len(papers) == 2

    a = papers[0]
    assert a.title == "AgentBench: Evaluating LLMs as Agents"
    assert [au.name for au in a.authors] == ["X Liu", "H Yu"]
    assert a.year == 2023
    assert a.citation_count == 684
    assert a.pdf_url == "https://arxiv.org/pdf/2308.03688.pdf"
    assert a.url == "https://arxiv.org/abs/2308.03688"

    b = papers[1]
    assert b.year == 2019
    assert b.citation_count == 185
    assert b.pdf_url is None
    # Authors parsed from the summary's leading chunk.
    assert "S Omidshafiei" in [au.name for au in b.authors]


@pytest.mark.asyncio
async def test_serpapi_no_key_is_noop(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("SERPAPI_KEY", raising=False)
    p = SerpApiScholarProvider(api_key="")
    assert p.available is False
    assert await p.search("anything") == []


@pytest.mark.asyncio
async def test_serpapi_surfaces_api_error(monkeypatch):
    _patch_httpx(monkeypatch, {"error": "Invalid API key"})
    p = SerpApiScholarProvider(api_key="bad")
    assert await p.search("q") == []


# ---- chain fallback ----

class _StubBackend:
    def __init__(self, result=None, raises=False):
        self._result = result or []
        self._raises = raises
        self.called = False

    async def search(self, query, **kwargs):
        self.called = True
        if self._raises:
            raise RuntimeError("backend down")
        return self._result


@pytest.mark.asyncio
async def test_chain_uses_primary_when_nonempty():
    primary = _StubBackend(result=["paper"])
    backup = _StubBackend(result=["other"])
    chain = GoogleScholarChainProvider([primary, backup])
    out = await chain.search("q")
    assert out == ["paper"]
    assert primary.called and not backup.called


@pytest.mark.asyncio
async def test_chain_falls_back_when_primary_empty():
    primary = _StubBackend(result=[])
    backup = _StubBackend(result=["backup-paper"])
    chain = GoogleScholarChainProvider([primary, backup])
    out = await chain.search("q")
    assert out == ["backup-paper"]
    assert primary.called and backup.called


@pytest.mark.asyncio
async def test_chain_falls_back_when_primary_raises():
    primary = _StubBackend(raises=True)
    backup = _StubBackend(result=["backup-paper"])
    chain = GoogleScholarChainProvider([primary, backup])
    out = await chain.search("q")
    assert out == ["backup-paper"]
    assert backup.called


@pytest.mark.asyncio
async def test_chain_empty_when_all_fail():
    chain = GoogleScholarChainProvider([_StubBackend(result=[]), _StubBackend(raises=True)])
    assert await chain.search("q") == []
