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


def _doc(figure_refs=None):
    return DocumentChunk(
        id="c1", text="See Fig. 1.",
        metadata=ChunkMetadata(
            paper_id="doi:10.1/x", chunk_index=0,
            figure_refs=figure_refs or [],
        ),
    )


@pytest.mark.asyncio
async def test_generate_response_wraps_with_figures(tmp_path):
    from perspicacite.rag.modes import advanced as advanced_mode
    cfg = _capsule(tmp_path)
    mode = advanced_mode.AdvancedRAGMode(cfg)

    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"
    request.kb_name = "kb1"
    request.kb_scope = "x"

    await mode._generate_response("q", [_doc(["pdf_p1_i0"])], llm, request)

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_generate_response_text_only_without_refs(tmp_path):
    from perspicacite.rag.modes import advanced as advanced_mode
    cfg = _capsule(tmp_path)
    mode = advanced_mode.AdvancedRAGMode(cfg)

    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"
    request.kb_name = "kb1"
    request.kb_scope = "x"

    await mode._generate_response("q", [_doc()], llm, request)
    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], str)


@pytest.mark.asyncio
async def test_generate_response_from_context_wraps_with_source_documents(tmp_path):
    from perspicacite.rag.modes import advanced as advanced_mode
    cfg = _capsule(tmp_path)
    mode = advanced_mode.AdvancedRAGMode(cfg)

    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"
    request.kb_name = "kb1"
    request.kb_scope = "x"
    request.refined_query = "q"
    request.query = "q"
    request.conversation_history = None

    await mode._generate_response_from_context(
        query="q", context="ctx", llm=llm, request=request,
        num_papers=1, source_documents=[_doc(["pdf_p1_i0"])], paper_results=None,
    )

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])
