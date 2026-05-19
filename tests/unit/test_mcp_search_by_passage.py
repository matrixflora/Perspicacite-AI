"""Tests for the ``search_by_passage`` MCP tool."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.fixture
def mock_state():
    state = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(
        return_value=MagicMock(embedding_model="text-embedding-3-small")
    )
    state.vector_store = MagicMock()
    state.embedding_provider = MagicMock(dimension=1536)
    return state


async def test_search_by_passage_returns_matches(mock_state):
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        fake_results = [
            {
                "text": "Temperature affects neural training stability.",
                "score": 0.88,
                "paper_id": "10.x/y",
                "metadata": {
                    "doi": "10.x/y",
                    "title": "T",
                    "year": 2024,
                    "license_id": "CC-BY",
                },
                "kb_name": "kb_a",
            }
        ]
        with patch(
            "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase.search",
            new=AsyncMock(return_value=fake_results),
        ):
            raw = await mcp_server.search_by_passage(
                text="how does temperature affect training?",
                kb_name="kb_a",
                k=3,
            )

    payload = json.loads(raw)
    assert payload["success"] is True
    results = payload["results"]
    assert len(results) == 1
    assert results[0]["chunk_text"].startswith("Temperature affects")
    assert results[0]["source"]["license_id"] == "CC-BY"
    assert results[0]["score"] == pytest.approx(0.88)


async def test_search_by_passage_rejects_empty(mock_state):
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        raw = await mcp_server.search_by_passage(text="", kb_name="kb_a")
    payload = json.loads(raw)
    assert payload["success"] is False
    assert "empty" in payload["error"].lower()


async def test_search_by_passage_unknown_kb_returns_error(mock_state):
    mock_state.session_store.get_kb_metadata = AsyncMock(return_value=None)
    with patch.object(mcp_server, "_require_state", return_value=mock_state):
        raw = await mcp_server.search_by_passage(text="hi", kb_name="ghost")
    payload = json.loads(raw)
    assert payload["success"] is False
    assert "ghost" in payload["error"]
