# tests/unit/test_europepmc_search.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.models.papers import PaperSource

_SAMPLE_RESPONSE = {
    "resultList": {
        "result": [
            {
                "id": "PMC1234567",
                "title": "Gut microbiome and health",
                "authorString": "Smith J, Jones A, Brown K",
                "journalTitle": "Nature",
                "pubYear": "2023",
                "doi": "10.1038/nature12345",
                "pmid": "98765432",
                "abstractText": "The gut microbiome plays a key role in health.",
                "isOpenAccess": "Y",
            },
            {
                "id": "PMC9999999",
                "title": "No DOI paper",
                "authorString": "Doe J",
                "journalTitle": "Science",
                "pubYear": "2022",
                "abstractText": "Abstract text.",
                "isOpenAccess": "N",
            },
        ]
    }
}


def _mock_response(data: dict, status: int = 200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    mock.raise_for_status = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_search_returns_papers():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response(_SAMPLE_RESPONSE))
        mock_client_cls.return_value = mock_client

        papers = await provider.search("gut microbiome", max_results=10)

    assert len(papers) == 2
    assert papers[0].doi == "10.1038/nature12345"
    assert papers[0].title == "Gut microbiome and health"
    assert papers[0].year == 2023
    assert papers[0].journal == "Nature"
    assert papers[0].source == PaperSource.EUROPE_PMC
    assert len(papers[0].authors) == 3
    assert papers[0].authors[0].name == "Smith J"


@pytest.mark.asyncio
async def test_search_with_year_filter():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response({"resultList": {"result": []}}))
        mock_client_cls.return_value = mock_client

        await provider.search("query", year_min=2020, year_max=2023)

        call_kwargs = mock_client.get.call_args
        params = call_kwargs.kwargs.get("params") or {}
        assert "2020" in str(params)


@pytest.mark.asyncio
async def test_search_empty_result():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()
    empty = {"resultList": {"result": []}}

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=_mock_response(empty))
        mock_client_cls.return_value = mock_client

        papers = await provider.search("nonexistent topic xyz")
    assert papers == []


@pytest.mark.asyncio
async def test_http_error_returns_empty():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    provider = EuropePMCSearchProvider()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(side_effect=Exception("network error"))
        mock_client_cls.return_value = mock_client

        papers = await provider.search("query")
    assert papers == []


def test_provider_metadata():
    from perspicacite.search.europepmc_search import EuropePMCSearchProvider
    p = EuropePMCSearchProvider()
    assert p.name == "europepmc"
    assert "biomedical" in p.domains
    assert p.tier == "reliable"
    assert p.retry == 0
