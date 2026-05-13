"""Tests for ProvenanceCollector wiring into RAGEngine.query_stream / query."""

from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest

from perspicacite.memory.session_store import SessionStore
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.provenance.context import get_collector
from perspicacite.provenance.store import ProvenanceStore
from perspicacite.rag.engine import RAGEngine


class _RecordingMode:
    """Tiny stand-in mode that asserts a collector is active during execute_stream."""

    seen: list[Any] = []

    async def execute_stream(self, request, llm, vector_store, embedding_provider, tools) -> AsyncIterator[StreamEvent]:
        c = get_collector()
        _RecordingMode.seen.append(c)
        yield StreamEvent(event="content", data='{"delta": "ok"}')
        yield StreamEvent(event="done", data="{}")


@pytest.mark.asyncio
async def test_engine_binds_and_saves_collector(tmp_path: Path) -> None:
    _RecordingMode.seen.clear()
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")

    cfg = MagicMock()
    cfg.rag_modes = MagicMock()
    engine = RAGEngine(
        llm_client=MagicMock(),
        vector_store=MagicMock(),
        embedding_provider=MagicMock(),
        tool_registry=MagicMock(),
        config=cfg,
    )
    engine._modes[RAGMode.BASIC] = _RecordingMode()  # type: ignore[assignment]
    engine.provenance_store = ps

    req = RAGRequest(query="hi", mode=RAGMode.BASIC, kb_name="default", top_k=5)
    events: list[StreamEvent] = []
    async for ev in engine.query_stream(req, message_id="msg-123", conversation_id="conv-1"):
        events.append(ev)

    assert _RecordingMode.seen and _RecordingMode.seen[0] is not None
    assert _RecordingMode.seen[0].message_id == "msg-123"
    rec = await ps.get_for_message("msg-123")
    assert rec is not None
    assert rec["conversation_id"] == "conv-1"
    assert rec["rag_mode"] == "basic"


@pytest.mark.asyncio
async def test_engine_backwards_compatible_without_message_id(tmp_path: Path) -> None:
    """Existing callers (no message_id) must keep working."""
    _RecordingMode.seen.clear()
    cfg = MagicMock(); cfg.rag_modes = MagicMock()
    engine = RAGEngine(
        llm_client=MagicMock(), vector_store=MagicMock(),
        embedding_provider=MagicMock(), tool_registry=MagicMock(), config=cfg,
    )
    engine._modes[RAGMode.BASIC] = _RecordingMode()  # type: ignore[assignment]
    # No provenance_store attached, no message_id passed
    req = RAGRequest(query="hi", mode=RAGMode.BASIC, kb_name="default", top_k=5)
    events = [ev async for ev in engine.query_stream(req)]
    assert len(events) == 2
