"""Verify search_knowledge_base MCP tool wires year params (Wave 4.2)."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp.server import search_knowledge_base
from perspicacite.models.search import SearchFilters


def _state_with_mocked_kb():
    state = MagicMock()
    state.session_store.get_kb_metadata = AsyncMock(return_value=MagicMock(
        embedding_model="text-embedding-3-small",
        paper_count=10,
    ))
    return state


@pytest.mark.asyncio
async def test_year_params_become_search_filters(monkeypatch):
    state = _state_with_mocked_kb()
    # Patch _require_state to return our mock.
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured_filters: list[SearchFilters | None] = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.config = MagicMock()
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured_filters.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    result = await search_knowledge_base(
        query="q", kb_name="kb1", top_k=5,
        year_min=2018, year_max=2023,
    )
    assert "results" in json.loads(result)
    assert len(captured_filters) == 1
    f = captured_filters[0]
    assert f is not None
    assert f.year_min == 2018
    assert f.year_max == 2023


@pytest.mark.asyncio
async def test_no_year_params_passes_no_filters(monkeypatch):
    state = _state_with_mocked_kb()
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured_filters: list = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured_filters.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    await search_knowledge_base(query="q", kb_name="kb1", top_k=5)
    assert captured_filters == [None]


@pytest.mark.asyncio
async def test_only_year_min(monkeypatch):
    state = _state_with_mocked_kb()
    monkeypatch.setattr(
        "perspicacite.mcp.server._require_state",
        lambda: state,
    )

    captured: list = []

    class _FakeDKB:
        def __init__(self, *a, **kw):
            self.collection_name = "c"
            self._initialized = True

        async def search(self, query, top_k=None, min_score=None, filters=None):
            captured.append(filters)
            return []

    monkeypatch.setattr(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
        _FakeDKB,
    )

    await search_knowledge_base(query="q", kb_name="kb1", year_min=2020)
    assert captured[0] is not None
    assert captured[0].year_min == 2020
    assert captured[0].year_max is None
