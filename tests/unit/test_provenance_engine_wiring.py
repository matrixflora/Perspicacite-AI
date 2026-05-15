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


def test_mcp_state_has_provenance_store_field() -> None:
    """MCPState must expose a provenance_store attribute (defaults to None)."""
    from perspicacite.mcp.server import MCPState

    state = MCPState()
    # Attribute must exist and default to None
    assert hasattr(state, "provenance_store")
    assert state.provenance_store is None


@pytest.mark.asyncio
async def test_mcp_generate_report_wires_provenance_and_message_id(
    tmp_path: Path,
) -> None:
    """generate_report must attach provenance_store and pass a message_id so
    that every MCP RAG answer is visible in the audit trail."""
    import json as _json
    from unittest.mock import AsyncMock, patch

    import perspicacite.mcp.server as _srv
    from perspicacite.mcp.server import MCPState, generate_report
    from perspicacite.provenance.store import ProvenanceStore

    # Build a minimal MCPState
    # state.config uses MagicMock so mode-handler __init__ calls like
    # config.knowledge_base.default_top_k auto-create rather than raising
    # AttributeError. The llm fields are set to real strings because
    # RAGRequest validates provider / model as str (pydantic v2).
    state = MCPState()
    state.initialized = True
    state.config = MagicMock()
    state.config.llm.default_provider = "deepseek"
    state.config.llm.default_model = "deepseek-chat"
    state.llm_client = MagicMock()
    state.embedding_provider = MagicMock()
    state.vector_store = MagicMock()
    state.tool_registry = MagicMock()

    # Attach a real provenance store
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    state.provenance_store = ps

    # Mock session_store.get_kb_metadata
    ss_mock = AsyncMock()
    ss_mock.get_kb_metadata = AsyncMock(return_value={"name": "default", "embedding_model": "m"})
    state.session_store = ss_mock

    # Capture what generate_report passes to query_stream
    captured_provenance_stores: list[Any] = []
    captured_message_ids: list[Any] = []

    original_RAGEngine = RAGEngine

    class _CapturingRAGEngine(original_RAGEngine):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

        async def query_stream(self, req, *, message_id=None, conversation_id=None):
            # At call time, capture what was set on self
            captured_provenance_stores.append(self.provenance_store)
            captured_message_ids.append(message_id)
            yield StreamEvent(event="content", data='{"delta": "hello"}')
            yield StreamEvent(event="done", data="{}")

    # generate_report does `from perspicacite.rag.engine import RAGEngine` inside
    # the function body, so we patch the class on its source module
    import perspicacite.rag.engine as _engine_mod
    original_cls = _engine_mod.RAGEngine
    _engine_mod.RAGEngine = _CapturingRAGEngine  # type: ignore[assignment]
    try:
        with patch.object(_srv, "mcp_state", state):
            result_str = await generate_report(
                query="test query", kb_name="default", mode="advanced"
            )
    finally:
        _engine_mod.RAGEngine = original_cls

    result = _json.loads(result_str)

    # A message_id must have been generated and passed
    assert captured_message_ids, "query_stream was never called"
    msg_id = captured_message_ids[0]
    assert msg_id is not None and isinstance(msg_id, str) and len(msg_id) > 0, (
        f"message_id must be a non-empty string, got {msg_id!r}"
    )

    # The engine's provenance_store must match state.provenance_store
    assert captured_provenance_stores, "provenance_store was never captured"
    assert captured_provenance_stores[0] is ps, (
        "generate_report must wire engine.provenance_store = state.provenance_store"
    )

    # The returned JSON must include the message_id
    assert result.get("message_id") is not None, (
        "generate_report must include message_id in its success JSON"
    )
