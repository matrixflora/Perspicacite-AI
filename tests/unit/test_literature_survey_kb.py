"""Unit tests for LiteratureSurveyRAGMode KB-context and reference-storage methods.

All ChromaDB, retriever, and session-store calls are mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _make_mode():
    """Return a LiteratureSurveyRAGMode with default Config (no external services)."""
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    return LiteratureSurveyRAGMode(Config())


def _fake_request(kb_names: list[str], query: str = "protein folding"):
    req = MagicMock()
    req.kb_names = kb_names
    req.kb_name = kb_names[0] if kb_names else "default"
    req.query = query
    return req


# ── _prepare_kb_context ──────────────────────────────────────────────────────

async def test_prepare_kb_context_noop_without_kb_names():
    mode = _make_mode()
    ctx, ids = await mode._prepare_kb_context(
        _fake_request([]), MagicMock(), MagicMock()
    )
    assert ctx == ""
    assert ids == set()


async def test_prepare_kb_context_collects_paper_ids_from_chromadb():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[
            ("doi:10.1/a", "Paper A", 3),
            ("doi:10.1/b", "Paper B", 2),
        ]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        _ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert "doi:10.1/a" in ids
    assert "doi:10.1/b" in ids


async def test_prepare_kb_context_builds_context_block_from_retriever():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(return_value=[])

    fake_meta = MagicMock()
    fake_meta.title = "AlphaFold"
    fake_meta.year = 2021
    fake_meta.doi = "10.1038/s41586-021-03819-2"
    fake_result = {
        "paper_id": "doi:10.1038/s41586",
        "kb_name": "biology-kb",
        "metadata": fake_meta,
    }

    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[fake_result])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, _ids = await mode._prepare_kb_context(
            _fake_request(["biology-kb"]), mock_vs, MagicMock()
        )
    assert "AlphaFold" in ctx
    assert "biology-kb" in ctx


async def test_prepare_kb_context_returns_empty_context_on_retrieval_error():
    """Even if retriever raises, known_ids (from ChromaDB listing) should still return."""
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[("doi:10.1/a", "Paper A", 1)]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=RuntimeError("embed crash"))
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert ctx == ""          # context block empty on retriever error
    assert "doi:10.1/a" in ids  # IDs still collected from ChromaDB
