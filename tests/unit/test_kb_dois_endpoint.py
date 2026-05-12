"""Unit tests for POST /api/kb/{name}/dois endpoint.

Tests the add_dois_to_kb handler directly by monkeypatching app_state,
retrieve_paper_content, and DynamicKnowledgeBase.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException


# ---------------------------------------------------------------------------
# Helpers: fake dependencies
# ---------------------------------------------------------------------------


def _make_fake_app_state(kb=None, paper_exists=False):
    """Build a SimpleNamespace that mimics the relevant app_state surface."""
    session_store = AsyncMock()
    session_store.get_kb_metadata = AsyncMock(return_value=kb)
    if kb is not None:
        session_store.save_kb_metadata = AsyncMock()

    vector_store = AsyncMock()
    vector_store.paper_exists = AsyncMock(return_value=paper_exists)

    config = MagicMock()
    config.pdf_download = None

    return SimpleNamespace(
        session_store=session_store,
        vector_store=vector_store,
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
        pdf_downloader=MagicMock(),
        config=config,
    )


def _make_fake_kb():
    """Create a fake KB object."""
    kb = MagicMock()
    kb.collection_name = "test_collection"
    kb.paper_count = 0
    kb.chunk_count = 0
    return kb


def _make_paper_content(success=True, full_text="paper body text"):
    """Create a fake PaperContent-like object."""
    pc = MagicMock()
    pc.success = success
    pc.full_text = full_text
    pc.abstract = None
    pc.metadata = {"title": "T", "authors": ["A"], "year": 2020, "journal": "J"}
    pc.content_type = "full_text"
    pc.content_source = "pmc"
    return pc


# ---------------------------------------------------------------------------
# Tests: KB not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_kb_not_found_returns_error():
    """Handler returns an error dict when KB does not exist."""
    from perspicacite.web.routers import kb as kb_router

    fake_state = _make_fake_app_state(kb=None)

    with patch.object(kb_router, "app_state", fake_state):
        from perspicacite.web.routers.kb import KBAddDOIsRequest

        result = await kb_router.add_dois_to_kb("missing_kb", KBAddDOIsRequest(dois=["10.1/a"]))

    assert "error" in result
    assert "not found" in result["error"].lower()


# ---------------------------------------------------------------------------
# Tests: oversized request
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversize_dois_raises_400():
    """Requests with >200 DOIs raise HTTPException(400)."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_state = _make_fake_app_state(kb=fake_kb)

    with patch.object(kb_router, "app_state", fake_state):
        from perspicacite.web.routers.kb import KBAddDOIsRequest

        with pytest.raises(HTTPException) as exc_info:
            await kb_router.add_dois_to_kb(
                "any_kb",
                KBAddDOIsRequest(dois=["10.1/x"] * 201),
            )

    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_adds_papers(monkeypatch):
    """Two DOIs, both new → added_papers=2, added_chunks=6, skipped=0."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_state = _make_fake_app_state(kb=fake_kb, paper_exists=False)
    fake_state.session_store.get_kb_metadata = AsyncMock(return_value=fake_kb)
    fake_state.session_store.save_kb_metadata = AsyncMock()

    fake_content = _make_paper_content(success=True, full_text="some text")

    async def fake_retrieve(doi, **kw):
        return fake_content

    fake_dkb_instance = AsyncMock()
    fake_dkb_instance.add_papers = AsyncMock(return_value=3)
    fake_dkb_instance.collection_name = None
    fake_dkb_instance._initialized = False

    FakeDKBClass = MagicMock(return_value=fake_dkb_instance)

    with patch.object(kb_router, "app_state", fake_state):
        # Patch the lazy imports at their source module so the function picks them up
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            with patch("perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", FakeDKBClass):
                from perspicacite.web.routers.kb import KBAddDOIsRequest

                result = await kb_router.add_dois_to_kb(
                    "default",
                    KBAddDOIsRequest(dois=["10.1/a", "10.1/b"]),
                )

    assert result["added_papers"] == 2, result
    # add_papers mock returns 3 (total chunks for the batch, not per-paper)
    assert result["added_chunks"] == 3, result
    assert result["skipped_duplicates"] == 0, result
    assert result["kb"] == "default"
    fake_state.session_store.save_kb_metadata.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dedup_skips_existing_doi(monkeypatch):
    """One of two DOIs already exists → added_papers=1, skipped=1."""
    from perspicacite.web.routers import kb as kb_router

    fake_kb = _make_fake_kb()
    fake_state = _make_fake_app_state(kb=fake_kb)
    fake_state.session_store.get_kb_metadata = AsyncMock(return_value=fake_kb)
    fake_state.session_store.save_kb_metadata = AsyncMock()

    call_count = {"n": 0}

    async def exists_for_first(collection_name, paper_id):
        call_count["n"] += 1
        # First DOI "10.1/a" exists; second "10.1/b" does not
        return paper_id == "10.1/a"

    fake_state.vector_store.paper_exists = exists_for_first

    fake_content = _make_paper_content(success=True, full_text="body")

    async def fake_retrieve(doi, **kw):
        return fake_content

    fake_dkb_instance = AsyncMock()
    fake_dkb_instance.add_papers = AsyncMock(return_value=3)
    fake_dkb_instance.collection_name = None
    fake_dkb_instance._initialized = False

    FakeDKBClass = MagicMock(return_value=fake_dkb_instance)

    with patch.object(kb_router, "app_state", fake_state):
        with patch("perspicacite.pipeline.download.retrieve_paper_content", fake_retrieve):
            with patch("perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", FakeDKBClass):
                from perspicacite.web.routers.kb import KBAddDOIsRequest

                result = await kb_router.add_dois_to_kb(
                    "default",
                    KBAddDOIsRequest(dois=["10.1/a", "10.1/b"]),
                )

    assert result["added_papers"] == 1, result
    assert result["skipped_duplicates"] == 1, result
    assert result["kb"] == "default"
