"""Verify ingest_dois_into_kb emits KBLog events (Wave 4.3)."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.search_to_kb import ingest_dois_into_kb


def _app_state(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        config=SimpleNamespace(
            pdf_download=None,
            kb=SimpleNamespace(
                checkpoint_dir=tmp_path / "ckpt",
                log_dir=tmp_path / "logs",
            ),
        ),
        session_store=MagicMock(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
                paper_count=0, chunk_count=0,
            )),
            save_kb_metadata=AsyncMock(),
        ),
        vector_store=MagicMock(paper_exists=AsyncMock(return_value=False)),
        embedding_provider=MagicMock(),
        pdf_parser=MagicMock(),
    )


@pytest.mark.asyncio
async def test_paper_added_event_recorded_on_success(tmp_path):
    state = _app_state(tmp_path)

    async def fake_retrieve(doi, **kw):
        return SimpleNamespace(
            success=True, full_text="x", abstract=None, metadata={"title": "T"},
        )

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx, patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase",
    ) as mock_dkb:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_dkb.return_value.add_papers = AsyncMock(return_value=5)

        await ingest_dois_into_kb(state, "kb1", ["10.1/a"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    assert log_path.exists()
    lines = log_path.read_text().strip().split("\n")
    events = [json.loads(l) for l in lines]
    kinds = [e["event"] for e in events]
    assert "paper_added" in kinds
    added = next(e for e in events if e["event"] == "paper_added")
    assert added["paper_id"] == "10.1/a"
    assert added["source_command"] == "ingest_dois_into_kb"


@pytest.mark.asyncio
async def test_paper_skipped_event_for_duplicate(tmp_path):
    state = _app_state(tmp_path)
    # Pretend the paper already exists.
    state.vector_store.paper_exists = AsyncMock(return_value=True)

    with patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await ingest_dois_into_kb(state, "kb1", ["10.1/dup"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    events = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
    assert any(e["event"] == "paper_skipped" and e["paper_id"] == "10.1/dup" for e in events)


@pytest.mark.asyncio
async def test_paper_failed_event_with_reason(tmp_path):
    state = _app_state(tmp_path)

    async def fake_retrieve(doi, **kw):
        raise RuntimeError("network down")

    with patch(
        "perspicacite.pipeline.download.retrieve_paper_content",
        new=fake_retrieve,
    ), patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
    ) as ctx:
        ctx.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await ingest_dois_into_kb(state, "kb1", ["10.1/x"])

    log_path = tmp_path / "logs" / "kb1.jsonl"
    events = [json.loads(l) for l in log_path.read_text().strip().split("\n")]
    failed = [e for e in events if e["event"] == "paper_failed"]
    assert len(failed) == 1
    assert failed[0]["paper_id"] == "10.1/x"
    assert "network down" in (failed[0].get("reason") or "")
