"""Tests for the agentic mode multimodal hook (Task 9, Cycle B)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk


def _capsule(tmp_path):
    cfg = Config()
    cfg.capsule.root = tmp_path / "capsules"
    cfg.capsule.root.mkdir(parents=True)
    safe = "doi_10.1__x"
    fig_dir = cfg.capsule.root / safe / "figures"
    fig_dir.mkdir(parents=True)
    fig_dir.joinpath("index.json").write_text(json.dumps([
        {"filename": "fig_p001_i00.png", "page": 1, "index": 0,
         "figure_number": "1", "caption": "C", "subcomponent_label": "",
         "panel_files": []}
    ]))
    fig_dir.joinpath("fig_p001_i00.png").write_bytes(b"\x89PNGfake" + b"\x00" * 50)
    return cfg


def _doc():
    return DocumentChunk(
        id="c1", text="t",
        metadata=ChunkMetadata(
            paper_id="doi:10.1/x", chunk_index=0,
            figure_refs=["pdf_p1_i0"],
        ),
    )


@pytest.mark.asyncio
async def test_llm_adapter_passthrough_no_chunks(tmp_path):
    """Default LLMAdapter.complete (no chunks/config) behaves exactly as before."""
    from perspicacite.rag.agentic.llm_adapter import LLMAdapter

    captured = {}

    async def fake_client_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    client = MagicMock()
    client.complete = AsyncMock(side_effect=fake_client_complete)
    adapter = LLMAdapter(client=client, model="claude-3-5-sonnet-20241022", provider="anthropic")
    await adapter.complete("hello")
    assert captured["messages"] == [{"role": "user", "content": "hello"}]


@pytest.mark.asyncio
async def test_llm_adapter_multimodal_path_when_chunks_and_config(tmp_path):
    from perspicacite.rag.agentic.llm_adapter import LLMAdapter

    cfg = _capsule(tmp_path)
    captured = {}

    async def fake_client_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    client = MagicMock()
    client.complete = AsyncMock(side_effect=fake_client_complete)
    adapter = LLMAdapter(client=client, model="claude-3-5-sonnet-20241022", provider="anthropic")
    await adapter.complete("hello", chunks=[_doc()], config=cfg)

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_orchestrator_chunks_with_figure_refs_helper():
    """The orchestrator helper picks up DocumentChunk items with figure_refs from step_results."""
    from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

    cfg = MagicMock()
    orch = AgenticOrchestrator(
        llm_client=MagicMock(),
        tool_registry=MagicMock(),
        embedding_provider=MagicMock(),
        vector_store=MagicMock(),
        config=cfg,
    )
    chunks = orch._chunks_with_figure_refs({"retrieve": [_doc(), _doc()]})
    assert len(chunks) == 2

    chunks_empty = orch._chunks_with_figure_refs({"retrieve": [{"text": "no chunks here"}]})
    assert chunks_empty == []
