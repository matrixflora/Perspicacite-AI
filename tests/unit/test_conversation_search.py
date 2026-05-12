"""Tests for conversation full-text search (FTS5 + LIKE fallback)."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_conversation_search_fts(tmp_path):
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.models.messages import Message

    store = SessionStore(tmp_path / "t.db")
    await store.init_db()

    # create_conversation(session_id, kb_name, title)
    conv = await store.create_conversation(session_id="s1", title="Algae study", kb_name="default")
    cid = conv.id

    await store.add_message(
        cid,
        Message(
            role="user",
            content="Tell me about photosynthesis in green algae",
        ),
    )
    await store.add_message(
        cid,
        Message(
            role="assistant",
            content="Photosynthesis converts light into chemical energy.",
        ),
    )

    results = await store.search_conversations("photosynthesis")
    assert any(r["id"] == cid for r in results)
    assert "snippet" in results[0]

    assert await store.search_conversations("zzznotpresentzzz") == []


@pytest.mark.asyncio
async def test_search_conversations_empty_query(tmp_path):
    """Empty or whitespace query returns an empty list immediately."""
    from perspicacite.memory.session_store import SessionStore

    store = SessionStore(tmp_path / "empty.db")
    await store.init_db()

    assert await store.search_conversations("") == []
    assert await store.search_conversations("   ") == []


@pytest.mark.asyncio
async def test_search_conversations_route(monkeypatch):
    from perspicacite.web.routers import conversations as conv_router

    class _SS:
        async def search_conversations(self, q, limit=20):
            return (
                [{"id": "c1", "title": "T", "snippet": "...photosynthesis..."}]
                if "photo" in q
                else []
            )

    monkeypatch.setattr(
        conv_router,
        "app_state",
        type("S", (), {"session_store": _SS()})(),
    )

    out = await conv_router.search_conversations(q="photosynthesis")
    assert out["results"] and out["results"][0]["id"] == "c1"

    assert (await conv_router.search_conversations(q=""))["results"] == []


@pytest.mark.asyncio
async def test_search_conversations_route_no_store(monkeypatch):
    """Route returns empty results when session_store is None."""
    from perspicacite.web.routers import conversations as conv_router

    monkeypatch.setattr(
        conv_router,
        "app_state",
        type("S", (), {"session_store": None})(),
    )

    out = await conv_router.search_conversations(q="anything")
    assert out == {"results": []}


@pytest.mark.asyncio
async def test_search_excludes_deleted_conversations(tmp_path):
    """Deleted conversations must not appear in FTS search results."""
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.models.messages import Message

    store = SessionStore(tmp_path / "t.db")
    await store.init_db()

    conv = await store.create_conversation(session_id="s1", title="Algae", kb_name="default")
    cid = conv.id

    await store.add_message(cid, Message(role="user", content="photosynthesis in algae"))

    # Conversation should be findable before deletion
    assert any(r["id"] == cid for r in await store.search_conversations("photosynthesis"))

    # Delete conversation — FTS index must be purged too
    await store.delete_conversation(cid)

    # Deleted conversation must not appear in search results
    assert all(r["id"] != cid for r in await store.search_conversations("photosynthesis"))
