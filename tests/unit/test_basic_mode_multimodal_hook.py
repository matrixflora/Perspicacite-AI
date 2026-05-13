import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.documents import ChunkMetadata, DocumentChunk


@pytest.mark.asyncio
async def test_basic_mode_routes_to_multimodal_when_figure_refs(tmp_path):
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

    from perspicacite.rag.modes import basic as basic_mode

    mode = basic_mode.BasicRAGMode(cfg)
    captured: dict = {}

    async def fake_complete(*, messages, model, provider, **kw):
        captured["messages"] = messages
        captured["model"] = model
        return "answer pdf_p1_i0"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)

    doc = DocumentChunk(
        id="c1", text="See Fig. 1.",
        metadata=ChunkMetadata(
            paper_id="doi:10.1/x", chunk_index=0, figure_refs=["pdf_p1_i0"]),
    )
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"

    await mode._generate_response("q", [doc], llm, request)

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_basic_mode_text_only_when_no_figure_refs(tmp_path):
    cfg = Config()
    cfg.capsule.root = tmp_path / "capsules"
    cfg.capsule.root.mkdir(parents=True)

    from perspicacite.rag.modes import basic as basic_mode
    mode = basic_mode.BasicRAGMode(cfg)

    captured: dict = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "ok"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)

    doc = DocumentChunk(
        id="c1", text="no figure here",
        metadata=ChunkMetadata(paper_id="doi:10.1/x", chunk_index=0),
    )
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"

    await mode._generate_response("q", [doc], llm, request)
    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], str)
