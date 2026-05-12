"""Tests for conversation Markdown export endpoint."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_conversation_export_markdown(monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from perspicacite.web.routers import conversations as conv_router

    conv = SimpleNamespace(
        id="abc",
        title="My Chat",
        kb_name="default",
        created_at=None,
        messages=[
            SimpleNamespace(
                role="user",
                content="What is X?",
                sources=[],
                metadata={},
            ),
            SimpleNamespace(
                role="assistant",
                content="X is a thing.",
                sources=[{"title": "Paper A", "doi": "10.1/a"}],
                metadata={},
            ),
            SimpleNamespace(
                role="user",
                content="And Y?",
                sources=[],
                metadata={},
            ),
            SimpleNamespace(
                role="assistant",
                content="Y is another thing.",
                sources=[{"title": "Paper B", "doi": "10.1/b"}],
                metadata={},
            ),
        ],
    )

    class _SS:
        async def get_conversation(self, cid):
            return conv if cid == "abc" else None

    monkeypatch.setattr(
        conv_router,
        "app_state",
        SimpleNamespace(session_store=_SS()),
    )

    resp = await conv_router.export_conversation(conv_id="abc", format="markdown")
    # PlainTextResponse.body is bytes
    body = resp.body.decode() if hasattr(resp, "body") else str(resp)

    assert "# My Chat" in body
    assert "What is X?" in body
    assert "X is a thing." in body
    assert "And Y?" in body
    assert "Y is another thing." in body
    assert "Paper A" in body
    assert "Paper B" in body
    assert ("References" in body) or ("Sources" in body)
    assert resp.media_type == "text/markdown"

    # 404 for unknown conversation
    with pytest.raises(HTTPException) as exc_info:
        await conv_router.export_conversation(conv_id="nope", format="markdown")
    assert exc_info.value.status_code == 404

    # 400 for unsupported format
    with pytest.raises(HTTPException) as exc_info:
        await conv_router.export_conversation(conv_id="abc", format="pdf")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_export_sources_as_json_string(monkeypatch):
    """Sources stored as JSON string (as in the DB) must be parsed correctly."""
    import json
    from types import SimpleNamespace

    from perspicacite.web.routers import conversations as conv_router

    conv = SimpleNamespace(
        id="x1",
        title="JSON sources test",
        kb_name="default",
        created_at=None,
        messages=[
            SimpleNamespace(role="user", content="Question?", sources=[], metadata={}),
            SimpleNamespace(
                role="assistant",
                content="Answer.",
                sources=json.dumps([{"title": "Serialized Paper", "doi": "10.1/s"}]),
                metadata={},
            ),
        ],
    )

    class _SS:
        async def get_conversation(self, cid):
            return conv

    monkeypatch.setattr(
        conv_router,
        "app_state",
        SimpleNamespace(session_store=_SS()),
    )

    resp = await conv_router.export_conversation(conv_id="x1", format="markdown")
    body = resp.body.decode()
    assert "Serialized Paper" in body


@pytest.mark.asyncio
async def test_export_no_store(monkeypatch):
    """503 when session_store is None."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from perspicacite.web.routers import conversations as conv_router

    monkeypatch.setattr(
        conv_router,
        "app_state",
        SimpleNamespace(session_store=None),
    )

    with pytest.raises(HTTPException) as exc_info:
        await conv_router.export_conversation(conv_id="any", format="markdown")
    assert exc_info.value.status_code == 503
