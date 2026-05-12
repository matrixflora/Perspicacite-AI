"""Unit tests for GET /api/kb/{name}/stats endpoint.

Tests the get_kb_stats handler directly by monkeypatching app_state.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers: fake dependencies
# ---------------------------------------------------------------------------


def _make_fake_collection(chunk_count=4, metadatas=None):
    """Build a fake ChromaDB collection."""
    coll = MagicMock()
    coll.count.return_value = chunk_count
    if metadatas is None:
        metadatas = [
            {
                "paper_id": "p1",
                "year": 2020,
                "source": "web_search",
                "title": "A",
                "journal": "J",
                "doi": "10.1/a",
            },
            {"paper_id": "p1", "year": 2020, "source": "web_search"},
            {"paper_id": "p2", "year": 2019, "source": "bibtex", "journal": "K"},
        ]
    ids = [f"chunk_{i}" for i in range(len(metadatas))]
    coll.get.return_value = {"ids": ids, "metadatas": metadatas}
    return coll


def _make_fake_app_state(kb=None, collection=None):
    """Build a SimpleNamespace that mimics the relevant app_state surface."""
    session_store = AsyncMock()
    session_store.get_kb_metadata = AsyncMock(return_value=kb)

    vector_store = MagicMock()
    if collection is not None:
        vector_store.client.get_collection.return_value = collection
    else:
        vector_store.client.get_collection.side_effect = Exception("collection not found")

    return SimpleNamespace(
        session_store=session_store,
        vector_store=vector_store,
    )


def _make_fake_kb():
    """Create a fake KB SimpleNamespace."""
    return SimpleNamespace(
        name="default",
        collection_name="c",
        embedding_model="m",
        paper_count=2,
        chunk_count=4,
        created_at=None,
    )


# ---------------------------------------------------------------------------
# Tests: KB not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_kb_stats_not_found():
    """Returns error dict when KB does not exist."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state(kb=None)

    with patch.object(kb_router, "app_state", fake_state):
        result = await kb_router.get_kb_stats("nope")

    assert "error" in result
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_kb_stats_happy_path():
    """Happy path returns expected aggregate stats."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_coll = _make_fake_collection(chunk_count=4)
    fake_state = _make_fake_app_state(kb=fake_kb, collection=fake_coll)

    with patch.object(kb_router, "app_state", fake_state):
        result = await kb_router.get_kb_stats("default")

    assert result["paper_count"] >= 1, result
    assert "by_year" in result
    assert isinstance(result["by_year"], dict)
    # Two unique paper_ids: p1 (year 2020) and p2 (year 2019)
    assert result["by_year"].get("2020") == 1, result["by_year"]
    assert result["by_year"].get("2019") == 1, result["by_year"]
    assert "by_source" in result
    assert "by_content_type" in result
    assert "top_journals" in result
    assert isinstance(result["top_journals"], list)
    assert result["embedding_model"] == "m"
    assert result["chunk_count"] == 4


@pytest.mark.asyncio
async def test_get_kb_stats_deduplication():
    """Stats counts each paper_id only once across chunks."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_coll = _make_fake_collection(chunk_count=4)
    fake_state = _make_fake_app_state(kb=fake_kb, collection=fake_coll)

    with patch.object(kb_router, "app_state", fake_state):
        result = await kb_router.get_kb_stats("default")

    # p1 appears twice in metadatas but should count once
    assert result["paper_count"] == 2, result


@pytest.mark.asyncio
async def test_get_kb_stats_top_journals():
    """top_journals is a list of dicts with journal/count keys."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_coll = _make_fake_collection(chunk_count=4)
    fake_state = _make_fake_app_state(kb=fake_kb, collection=fake_coll)

    with patch.object(kb_router, "app_state", fake_state):
        result = await kb_router.get_kb_stats("default")

    journals = result["top_journals"]
    assert all("journal" in j and "count" in j for j in journals)
    journal_names = [j["journal"] for j in journals]
    assert "J" in journal_names or "K" in journal_names


@pytest.mark.asyncio
async def test_get_kb_stats_collection_error_handled():
    """If collection scan fails, handler returns gracefully with zeros."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    # collection=None causes get_collection to raise
    fake_state = _make_fake_app_state(kb=fake_kb, collection=None)

    with patch.object(kb_router, "app_state", fake_state):
        result = await kb_router.get_kb_stats("default")

    # Should not raise, should return something sensible
    assert "error" not in result or "collection" in result.get("error", "").lower()
    assert "chunk_count" in result or "paper_count" in result
