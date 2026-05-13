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
async def test_fallback_stream_wraps_when_figure_refs(tmp_path):
    """_fallback_answer_stream wraps messages with figures when chunks carry refs."""
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    cfg = _capsule(tmp_path)
    mode = ContradictionRAGMode(cfg)
    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "answer"

    llm = MagicMock(spec=["complete"])  # no stream() — forces complete() fallback
    llm.complete = AsyncMock(side_effect=fake_complete)

    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.query = "q"

    events = []
    async for ev in mode._fallback_answer_stream(request, [_doc(["pdf_p1_i0"])], llm):
        events.append(ev)

    assert captured.get("messages"), "complete() was never called"
    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])


@pytest.mark.asyncio
async def test_fallback_stream_text_only_no_refs(tmp_path):
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    cfg = _capsule(tmp_path)
    mode = ContradictionRAGMode(cfg)
    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "answer"

    llm = MagicMock(spec=["complete"])
    llm.complete = AsyncMock(side_effect=fake_complete)

    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.query = "q"

    events = []
    async for ev in mode._fallback_answer_stream(request, [_doc()], llm):
        events.append(ev)

    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], str)


@pytest.mark.asyncio
async def test_synthesize_stream_wraps_when_figure_refs(tmp_path):
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    cfg = _capsule(tmp_path)
    mode = ContradictionRAGMode(cfg)
    captured = {}

    async def fake_complete(*, messages, **kw):
        captured["messages"] = messages
        return "answer"

    llm = MagicMock(spec=["complete"])
    llm.complete = AsyncMock(side_effect=fake_complete)

    request = MagicMock()
    request.model = "claude-3-5-sonnet-20241022"
    request.query = "q"

    docs = [_doc(["pdf_p1_i0"])]
    events = []
    async for ev in mode._synthesize_stream(
        request, "q", {"agreement": [], "disagreement": [], "open": []},
        [{"title": "T", "doi": "10.1/x", "claims": "c1"}], docs, llm,
    ):
        events.append(ev)

    assert captured.get("messages")
    user_msg = next(m for m in reversed(captured["messages"]) if m["role"] == "user")
    assert isinstance(user_msg["content"], list)
    assert any(p.get("type") == "image_url" for p in user_msg["content"])
