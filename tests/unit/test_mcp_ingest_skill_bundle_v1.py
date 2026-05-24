"""Tests for ingest_skill_bundle MCP tool source_format dispatch."""
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE = str(
    Path(__file__).parent.parent / "fixtures" / "asb" / "skill_collection_v1"
)


def _make_state():
    state = MagicMock()
    state.vector_store = MagicMock()
    state.embedding_provider = MagicMock()
    state.session_store = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_ingest_skill_bundle_default_format_uses_legacy():
    """Default source_format='legacy' should call legacy pipeline, not v1."""
    from perspicacite.mcp.server import ingest_skill_bundle
    from perspicacite.pipeline.github_kb import IngestSummary

    with (
        patch("perspicacite.mcp.server._require_state", return_value=_make_state()),
        patch(
            "perspicacite.mcp.server.ingest_skill_bundle_pipeline",
            new_callable=AsyncMock,
        ) as legacy_mock,
    ):
        legacy_mock.return_value = IngestSummary(
            kb_name="kb",
            bundle_name="bundle",
            repo_org=None,
            repo_name=None,
            commit_sha=None,
            files_added=1,
            chunks_added=10,
            linked_papers_added=0,
            linked_papers_skipped_non_doi=[],
            mode="per-skill",
        )
        result = await ingest_skill_bundle(
            source="/tmp/fake-bundle",
            kb_name="test-kb",
        )

    legacy_mock.assert_called_once()
    data = json.loads(result)
    assert data["success"] is True


@pytest.mark.asyncio
async def test_ingest_skill_bundle_v1_format_calls_collection_ingest():
    """source_format='asb-skill-collection-v1' should call ingest_asb_skill_collection."""
    from perspicacite.mcp.server import ingest_skill_bundle

    with (
        patch("perspicacite.mcp.server._require_state", return_value=_make_state()),
        patch(
            "perspicacite.mcp.server.ingest_asb_skill_collection",
            new_callable=AsyncMock,
        ) as v1_mock,
    ):
        v1_mock.return_value = {
            "kb_name": "test-kb",
            "collection_name": "metabolomics-v1",
            "skills_ingested": 1,
            "papers_added": 3,
            "failed": [],
            "kb_metadata_written": [],
        }
        result = await ingest_skill_bundle(
            source=FIXTURE,
            kb_name="test-kb",
            source_format="asb-skill-collection-v1",
        )

    v1_mock.assert_called_once()
    data = json.loads(result)
    assert data["success"] is True
    assert data.get("collection_name") == "metabolomics-v1"


@pytest.mark.asyncio
async def test_ingest_skill_bundle_invalid_format_returns_error():
    """Unknown source_format should return a JSON error immediately."""
    from perspicacite.mcp.server import ingest_skill_bundle

    with patch("perspicacite.mcp.server._require_state", return_value=_make_state()):
        result = await ingest_skill_bundle(
            source="/tmp/fake",
            source_format="unknown-format-xyz",
        )

    data = json.loads(result)
    assert data["success"] is False
    assert "source_format" in data.get("error", "").lower()
