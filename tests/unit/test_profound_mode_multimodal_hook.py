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
        id="c1", text="t",
        metadata=ChunkMetadata(
            paper_id="doi:10.1/x", chunk_index=0,
            figure_refs=figure_refs or [],
        ),
    )


@pytest.mark.asyncio
async def test_profound_final_draft_wraps_with_figures(tmp_path):
    from perspicacite.rag.modes import profound as profound_mode
    cfg = _capsule(tmp_path)
    mode = profound_mode.ProfoundRAGMode(cfg)

    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "answer"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"

    await mode._profound_final_draft_answer(
        "q", "research_text", [_doc(["pdf_p1_i0"])], llm, request, None,
    )

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_profound_final_draft_text_only_without_refs(tmp_path):
    from perspicacite.rag.modes import profound as profound_mode
    cfg = _capsule(tmp_path)
    mode = profound_mode.ProfoundRAGMode(cfg)

    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "answer"

    llm = MagicMock()
    llm.complete = AsyncMock(side_effect=fake_complete)
    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.provider = "anthropic"

    await mode._profound_final_draft_answer(
        "q", "research_text", [_doc()], llm, request, None,
    )
    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], str)
