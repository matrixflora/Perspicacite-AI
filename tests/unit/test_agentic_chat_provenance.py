from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_stream_agentic_binds_collector_and_saves(tmp_path: Path, monkeypatch) -> None:
    """The agentic chat path must bind a ProvenanceCollector and persist a
    record keyed by an assistant message id."""
    from perspicacite.web import state as state_mod
    from perspicacite.web.routers import chat as chat_router

    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)
    monkeypatch.setattr(state_mod.app_state, "provenance_store", ps, raising=False)

    seen_collector_ids: list[str | None] = []

    async def fake_chat(query, session_id, kb_name, stream, **kw):
        # Inside the orchestrator's flow, get_collector() must return a live
        # collector — capture its message_id so the test can assert.
        from perspicacite.provenance.context import get_collector
        c = get_collector()
        seen_collector_ids.append(c.message_id if c is not None else None)
        if c is not None:
            c.add_trace("intent", detail={"value": "fact"})
        yield {"type": "answer", "session_id": session_id, "content": "OK"}

    orchestrator = MagicMock()
    orchestrator.chat = fake_chat
    monkeypatch.setattr(state_mod.app_state, "orchestrator", orchestrator, raising=False)

    # Drive _stream_agentic
    req = MagicMock()
    req.query = "q"
    req.session_id = "s"
    req.kb_name = "default"
    req.kb_names = None
    req.max_papers_to_download = 0

    chunks: list[str] = []
    async for ev in chat_router._stream_agentic(req, conversation_id="conv-X"):
        chunks.append(ev)

    # The orchestrator saw a real collector with a non-None message_id
    assert seen_collector_ids and seen_collector_ids[0] is not None
    msg_id = seen_collector_ids[0]
    # That same message_id was emitted in the 'answer' and 'done' SSE frames
    joined = "\n".join(chunks)
    assert msg_id in joined
    # Provenance row persisted
    rec = await ps.get_for_message(msg_id)
    assert rec is not None
    assert rec["conversation_id"] == "conv-X"
    assert rec["rag_mode"] == "agentic"
    assert any(t["step"] == "intent" for t in rec["mode_trace"])
