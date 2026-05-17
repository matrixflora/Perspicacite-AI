"""Tests for the exclude_kb dedup parameter on search_literature."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_search_literature_exclude_kb_filters_existing_papers():
    """Papers whose DOI already exists in the specified KB must be dropped."""
    from perspicacite.mcp.server import search_literature

    existing_doi = "10.1/existing"
    new_doi = "10.2/new"

    from perspicacite.models.papers import Paper, PaperSource

    fake_papers = [
        Paper(id=existing_doi, title="Already in KB", doi=existing_doi,
              source=PaperSource.OPENALEX),
        Paper(id=new_doi, title="Not in KB", doi=new_doi,
              source=PaperSource.OPENALEX),
    ]

    mock_aggregator = MagicMock()
    mock_aggregator.available = True
    mock_aggregator.search = AsyncMock(return_value=fake_papers)
    mock_aggregator.last_errors_by_database = {}

    mock_state = MagicMock()
    mock_state.config = MagicMock()

    # paper_exists returns True only for the existing DOI
    async def fake_exists(collection, paper_id):
        return paper_id == existing_doi

    mock_state.vector_store.paper_exists = fake_exists

    with patch("perspicacite.mcp.server._require_state", return_value=mock_state), \
         patch(
             "perspicacite.search.domain_aggregator.build_aggregator",
             return_value=mock_aggregator,
         ):
        result_json = await search_literature(
            query="test query",
            max_results=10,
            exclude_kb="my-kb",
        )

    result = json.loads(result_json)
    titles = [p["title"] for p in result.get("papers", [])]
    assert "Already in KB" not in titles
    assert "Not in KB" in titles


@pytest.mark.asyncio
async def test_search_literature_no_exclude_kb_returns_all():
    """When exclude_kb is None (default), all results are returned unchanged."""
    from perspicacite.mcp.server import search_literature
    from perspicacite.models.papers import Paper, PaperSource

    fake_papers = [
        Paper(id="10.1/a", title="Paper A", doi="10.1/a", source=PaperSource.OPENALEX),
        Paper(id="10.2/b", title="Paper B", doi="10.2/b", source=PaperSource.OPENALEX),
    ]

    mock_aggregator = MagicMock()
    mock_aggregator.available = True
    mock_aggregator.search = AsyncMock(return_value=fake_papers)
    mock_aggregator.last_errors_by_database = {}

    mock_state = MagicMock()
    mock_state.config = MagicMock()

    with patch("perspicacite.mcp.server._require_state", return_value=mock_state), \
         patch(
             "perspicacite.search.domain_aggregator.build_aggregator",
             return_value=mock_aggregator,
         ):
        result_json = await search_literature(
            query="test query",
            max_results=10,
            exclude_kb=None,
        )

    result = json.loads(result_json)
    assert len(result.get("papers", [])) == 2
