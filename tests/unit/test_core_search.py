# tests/unit/test_core_search.py
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from perspicacite.models.papers import PaperSource

_SAMPLE_RESPONSE = {
    "results": [
        {
            "id": "123",
            "title": "Open access paper on machine learning",
            "authors": [{"name": "Smith, John"}, {"name": "Jones, Alice"}],
            "yearPublished": 2023,
            "doi": "10.1234/core.123",
            "abstract": "This paper discusses ML methods.",
            "downloadUrl": "https://core.ac.uk/download/pdf/123.pdf",
            "journals": [{"title": "Journal of ML"}],
        },
        {
            "id": "456",
            "title": "Another paper",
            "authors": [],
            "yearPublished": None,
            "doi": None,
            "abstract": None,
            "downloadUrl": None,
            "journals": [],
        },
    ],
    "totalHits": 2,
}


def _mock_resp(data: dict, status: int = 200):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.asyncio
async def test_search_returns_papers(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider

    async def mock_post(self, url, **kwargs):
        return _mock_resp(_SAMPLE_RESPONSE)

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider()
    papers = await provider.search("machine learning")
    assert len(papers) == 2
    assert papers[0].doi == "10.1234/core.123"
    assert papers[0].title == "Open access paper on machine learning"
    assert papers[0].year == 2023
    assert papers[0].journal == "Journal of ML"
    assert papers[0].source == PaperSource.CORE
    assert len(papers[0].authors) == 2
    assert papers[0].authors[0].name == "Smith, John"


@pytest.mark.asyncio
async def test_search_with_api_key_sets_auth_header(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider

    headers_sent: list[dict] = []

    async def mock_post(self, url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider(api_key="mykey123")
    await provider.search("test")
    assert any("Authorization" in h for h in headers_sent)
    assert any("mykey123" in str(h) for h in headers_sent)


@pytest.mark.asyncio
async def test_search_without_api_key_no_auth_header(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider

    headers_sent: list[dict] = []

    async def mock_post(self, url, *, headers=None, **kwargs):
        headers_sent.append(headers or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider(api_key=None)
    await provider.search("test")
    assert not any("Authorization" in h for h in headers_sent)


@pytest.mark.asyncio
async def test_year_filter_in_payload(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider

    payloads_sent: list[dict] = []

    async def mock_post(self, url, *, json=None, **kwargs):
        payloads_sent.append(json or {})
        return _mock_resp({"results": [], "totalHits": 0})

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider()
    await provider.search("test", year_min=2020, year_max=2023)
    assert payloads_sent
    payload_str = str(payloads_sent[0])
    assert "2020" in payload_str


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    from perspicacite.search.core_search import CORESearchProvider

    async def mock_post(self, url, **kwargs):
        raise Exception("network error")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    provider = CORESearchProvider()
    papers = await provider.search("test")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.core_search import CORESearchProvider
    p = CORESearchProvider()
    assert p.name == "core"
    assert "general" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
