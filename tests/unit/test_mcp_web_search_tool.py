"""Unit tests for the new web_search MCP tool."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.models.papers import Paper, Author
from perspicacite.mcp.server import web_search


@pytest.mark.asyncio
async def test_web_search_returns_serialised_papers():
    fake = [
        Paper(
            id="doi:10.1/x", title="Paper 1",
            authors=[Author(name="A. Author")],
            year=2024, doi="10.1/x", abstract="abs1",
        ),
        Paper(id="doi:10.1/y", title="Paper 2", doi="10.1/y"),
    ]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p: p),
    ):
        out = await web_search(query="q", databases=["openalex"])
    data = json.loads(out)
    assert len(data["papers"]) == 2
    assert data["papers"][0]["title"] == "Paper 1"
    assert data["papers"][0]["doi"] == "10.1/x"
    assert "telemetry_summary" in data


@pytest.mark.asyncio
async def test_web_search_skips_enrich_when_disabled():
    fake = [Paper(id="x", title="t")]
    mock_enrich = AsyncMock(side_effect=lambda p: p)
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        mock_enrich,
    ):
        await web_search(query="q", enrich=False)
    mock_enrich.assert_not_called()


@pytest.mark.asyncio
async def test_web_search_error_response():
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        out = await web_search(query="q")
    data = json.loads(out)
    assert "error" in data
    assert data["papers"] == []
