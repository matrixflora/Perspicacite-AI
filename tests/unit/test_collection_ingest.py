"""Tests for ASB-Skill collection v1 ingest orchestrator."""
import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "asb" / "skill_collection_v1"


def _copy_fixture(tmp_path: Path) -> Path:
    """Copy FIXTURE to tmp_path to avoid writing side-files into the repo."""
    dest = tmp_path / "collection"
    shutil.copytree(FIXTURE, dest)
    return dest


@pytest.fixture
def mock_app_state():
    state = MagicMock()
    state.vector_store = MagicMock()
    state.embedding_provider = MagicMock()
    state.session_store = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_ingest_collection_returns_summary(tmp_path, mock_app_state):
    from perspicacite.pipeline.asb.collection_ingest import ingest_asb_skill_collection

    target = _copy_fixture(tmp_path)

    with (
        patch(
            "perspicacite.pipeline.asb.collection_ingest._make_or_get_kb",
            new_callable=AsyncMock,
        ) as mk_kb,
        patch(
            "perspicacite.pipeline.asb.collection_ingest._ingest_backing_paper_dois",
            new_callable=AsyncMock,
        ),
    ):
        fake_kb = AsyncMock()
        fake_kb.add_papers = AsyncMock()
        mk_kb.return_value = fake_kb

        result = await ingest_asb_skill_collection(
            collection_dir=target,
            kb_name="test-collection-kb",
            app_state=mock_app_state,
            ingest_linked_papers=False,
        )

    assert result["kb_name"] == "test-collection-kb"
    assert result["skills_ingested"] >= 1
    assert "failed" in result


@pytest.mark.asyncio
async def test_ingest_collection_writes_ontology_refs(tmp_path, mock_app_state):
    """collection.yaml EDAM IRIs should land in kb_metadata/ontology_refs.json."""
    from perspicacite.pipeline.asb.collection_ingest import ingest_asb_skill_collection

    # Copy fixture to tmp_path so we can check file writes
    shutil.copytree(FIXTURE, tmp_path / "collection")
    target = tmp_path / "collection"

    with (
        patch(
            "perspicacite.pipeline.asb.collection_ingest._make_or_get_kb",
            new_callable=AsyncMock,
        ) as mk_kb,
        patch(
            "perspicacite.pipeline.asb.collection_ingest._ingest_backing_paper_dois",
            new_callable=AsyncMock,
        ),
    ):
        fake_kb = AsyncMock()
        fake_kb.add_papers = AsyncMock()
        mk_kb.return_value = fake_kb

        await ingest_asb_skill_collection(
            collection_dir=target,
            kb_name="test-kb",
            app_state=mock_app_state,
            ingest_linked_papers=False,
        )

    ontology_refs_path = target / "kb_metadata" / "ontology_refs.json"
    assert ontology_refs_path.exists(), "kb_metadata/ontology_refs.json should be written"
    data = json.loads(ontology_refs_path.read_text())
    assert "edam_topics" in data


@pytest.mark.asyncio
async def test_ingest_collection_writes_skill_index(tmp_path, mock_app_state):
    """catalogue.jsonld entries should land in kb_metadata/skill_index.json."""
    from perspicacite.pipeline.asb.collection_ingest import ingest_asb_skill_collection

    shutil.copytree(FIXTURE, tmp_path / "collection")
    target = tmp_path / "collection"

    with (
        patch(
            "perspicacite.pipeline.asb.collection_ingest._make_or_get_kb",
            new_callable=AsyncMock,
        ) as mk_kb,
        patch(
            "perspicacite.pipeline.asb.collection_ingest._ingest_backing_paper_dois",
            new_callable=AsyncMock,
        ),
    ):
        fake_kb = AsyncMock()
        fake_kb.add_papers = AsyncMock()
        mk_kb.return_value = fake_kb

        await ingest_asb_skill_collection(
            collection_dir=target,
            kb_name="test-kb",
            app_state=mock_app_state,
            ingest_linked_papers=False,
        )

    skill_index_path = target / "kb_metadata" / "skill_index.json"
    assert skill_index_path.exists(), "kb_metadata/skill_index.json should be written"
    data = json.loads(skill_index_path.read_text())
    assert "skills" in data


@pytest.mark.asyncio
async def test_ingest_collection_calls_doi_ingest_when_enabled(tmp_path, mock_app_state):
    from perspicacite.pipeline.asb.collection_ingest import ingest_asb_skill_collection

    target = _copy_fixture(tmp_path)

    with (
        patch(
            "perspicacite.pipeline.asb.collection_ingest._make_or_get_kb",
            new_callable=AsyncMock,
        ) as mk_kb,
        patch(
            "perspicacite.pipeline.asb.collection_ingest._ingest_backing_paper_dois",
            new_callable=AsyncMock,
        ) as mock_doi_ingest,
    ):
        fake_kb = AsyncMock()
        fake_kb.add_papers = AsyncMock()
        mk_kb.return_value = fake_kb

        await ingest_asb_skill_collection(
            collection_dir=target,
            kb_name="test-kb",
            app_state=mock_app_state,
            ingest_linked_papers=True,
        )

    # At least one skill has a DOI, so doi_ingest should be called
    mock_doi_ingest.assert_called()
