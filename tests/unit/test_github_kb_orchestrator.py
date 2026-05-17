"""Tests for github_kb orchestrator (mocked dependencies)."""
from __future__ import annotations

from pathlib import Path  # noqa: TC003
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.github_kb import (
    IngestSummary,
    ingest_skill_bundle,
    ingest_skill_bundles_batch,
)


def _make_bundle(tmp_path: Path, name: str = "test-bundle") -> Path:
    bundle_dir = tmp_path / name
    bundle_dir.mkdir()
    (bundle_dir / "bundle.yml").write_text(
        f"name: {name}\n"
        "papers:\n"
        "  - doi: 10.1/a\n"
        "  - doi: 10.2/b\n"
    )
    (bundle_dir / "README.md").write_text("# Bundle\nSee 10.3/c for details.")
    (bundle_dir / "main.py").write_text('"""Main module."""\n\ndef run():\n    """Run it."""\n    pass\n')  # noqa: E501
    return bundle_dir


def _mock_config(tmp_path: Path):
    return SimpleNamespace(
        knowledge_base=SimpleNamespace(log_dir=tmp_path / "logs"),
        github=SimpleNamespace(token_env_var="GITHUB_TOKEN", cache_dir=tmp_path / "cache"),
    )


@pytest.mark.asyncio
async def test_ingest_skill_bundle_calls_add_papers(tmp_path):
    bundle_dir = _make_bundle(tmp_path)
    config = _mock_config(tmp_path)

    captured_dois: list[str] = []

    async def fake_ingest(app_state, kb_name, dois, **kw):
        captured_dois.extend(dois)
        return {"added_papers": len(dois), "added_chunks": 0, "skipped_duplicates": 0, "failed": [], "pdf_download": {}}  # noqa: E501

    mock_dkb = MagicMock()
    mock_dkb.add_papers = AsyncMock(return_value=5)

    with patch("perspicacite.pipeline.search_to_kb.ingest_dois_into_kb", new=fake_ingest), \
         patch("perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", return_value=mock_dkb):
        summary = await ingest_skill_bundle(
            source=bundle_dir,
            kb_name="test-kb",
            config=config,
            vector_store=MagicMock(),
            embedding_service=MagicMock(),
            session_store=MagicMock(),
            ingest_linked_papers=True,
        )

    assert summary.files_added >= 2  # README.md + main.py
    # DOIs from manifest should have been ingested
    assert "10.1/a" in captured_dois
    assert "10.2/b" in captured_dois


@pytest.mark.asyncio
async def test_ingest_skill_bundle_no_linked_papers(tmp_path):
    bundle_dir = _make_bundle(tmp_path)
    config = _mock_config(tmp_path)
    mock_dkb = MagicMock()
    mock_dkb.add_papers = AsyncMock(return_value=3)

    with patch("perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", return_value=mock_dkb):
        summary = await ingest_skill_bundle(
            source=bundle_dir,
            kb_name="test-kb",
            config=config,
            vector_store=MagicMock(),
            embedding_service=MagicMock(),
            session_store=MagicMock(),
            ingest_linked_papers=False,
        )

    assert summary.linked_papers_added == 0


@pytest.mark.asyncio
async def test_ingest_skill_bundles_batch_processes_all(tmp_path):
    dirs = [_make_bundle(tmp_path, f"bundle-{i}") for i in range(3)]
    config = _mock_config(tmp_path)
    mock_dkb = MagicMock()
    mock_dkb.add_papers = AsyncMock(return_value=1)

    async def fake_ingest(app_state, kb_name, dois, **kw):
        return {"added_papers": len(dois), "added_chunks": 0, "skipped_duplicates": 0, "failed": [], "pdf_download": {}}  # noqa: E501

    with patch("perspicacite.pipeline.search_to_kb.ingest_dois_into_kb", new=fake_ingest), \
         patch("perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", return_value=mock_dkb):
        summaries = await ingest_skill_bundles_batch(
            sources=dirs,
            config=config,
            vector_store=MagicMock(),
            embedding_service=MagicMock(),
            session_store=MagicMock(),
            ingest_linked_papers=True,
        )

    assert len(summaries) == 3
    assert all(isinstance(s, IngestSummary) for s in summaries)
