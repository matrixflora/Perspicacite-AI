"""Unit tests for the new web_search MCP tool."""
import json
import pytest
from unittest.mock import patch, AsyncMock

from perspicacite.models.papers import Paper, Author
from perspicacite.search.screening import ScreenResult
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
    # When enrich=True the pipeline calls enrich_papers, then screen_papers_rerank.
    # Patch both so we get deterministic results without loading ML models.
    fake_screen = [
        ScreenResult(item={"_paper": fake[0], "title": "Paper 1", "abstract": "abs1"}, score=0.9, kept=True),
        ScreenResult(item={"_paper": fake[1], "title": "Paper 2", "abstract": ""}, score=0.4, kept=True),
    ]
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=fake),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p, **kw: p),
    ), patch(
        "perspicacite.search.screening.screen_papers_rerank",
        AsyncMock(return_value=fake_screen),
    ):
        out = await web_search(query="q", databases=["openalex"])
    data = json.loads(out)
    assert len(data["papers"]) == 2
    titles = {p["title"] for p in data["papers"]}
    assert "Paper 1" in titles
    assert data["papers"][0]["doi"] == "10.1/x"  # highest score lands first
    assert "telemetry_summary" in data


@pytest.mark.asyncio
async def test_web_search_skips_enrich_when_disabled():
    fake = [Paper(id="x", title="t")]
    mock_enrich = AsyncMock(side_effect=lambda p, **kw: p)
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
