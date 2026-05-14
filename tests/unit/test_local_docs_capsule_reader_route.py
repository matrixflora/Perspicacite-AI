"""Routing test: ingest_local_documents sends capsule dirs to CapsuleReader
and non-capsule paths to the per-file ingest path."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.integrations import local_docs as local_docs_mod


def _write_capsule(root: Path) -> Path:
    cap = root / "cap1"
    cap.mkdir()
    (cap / "metadata.json").write_text(
        json.dumps({"capsule_version": "0.1", "paper_id": "doi:10.1/x",
                    "title": "T", "authors": [], "year": 2025,
                    "doi": "10.1/x", "source": "local"})
    )
    (cap / "text").mkdir()
    (cap / "text" / "blocks.jsonl").write_text(
        json.dumps({"block_id": "b1", "section": "abstract", "page": 1,
                    "content": "abc " * 60, "figure_refs": []}) + "\n"
    )
    return cap


@pytest.mark.asyncio
async def test_capsule_dir_routes_to_capsule_reader(tmp_path):
    cap = _write_capsule(tmp_path)

    async def fake_ingest_capsule(*, capsule_dir, kb_name, app_state, registry, job_id, finalize):
        assert finalize is False
        assert capsule_dir == cap
        return {"added_chunks": 7, "files": 1}

    async def fake_ingest_files(*, kb_name, files, app_state, registry, job_id):
        raise AssertionError("should not be called for capsule-only input")

    registry = AsyncMock()

    with patch.object(local_docs_mod, "ingest_capsule",
                      side_effect=fake_ingest_capsule), \
         patch.object(local_docs_mod, "_ingest_files",
                      side_effect=fake_ingest_files):
        result = await local_docs_mod.ingest_local_documents(
            kb_name="kb1", paths=[cap], app_state=None,
            registry=registry, job_id="j1",
        )
    assert result == {"added_chunks": 7, "files": 1}
    registry.finish.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_capsule_paths_route_to_files(tmp_path):
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    async def fake_ingest_capsule(**kw):
        raise AssertionError("should not be called for pdf-only input")

    async def fake_ingest_files(*, kb_name, files, app_state, registry, job_id):
        assert pdf in list(files)
        return {"added_chunks": 4, "files": 1}

    registry = AsyncMock()

    with patch.object(local_docs_mod, "ingest_capsule",
                      side_effect=fake_ingest_capsule), \
         patch.object(local_docs_mod, "_ingest_files",
                      side_effect=fake_ingest_files):
        result = await local_docs_mod.ingest_local_documents(
            kb_name="kb1", paths=[pdf], app_state=None,
            registry=registry, job_id="j1",
        )
    assert result == {"added_chunks": 4, "files": 1}


@pytest.mark.asyncio
async def test_mixed_inputs_route_to_both(tmp_path):
    cap = _write_capsule(tmp_path)
    pdf = tmp_path / "y.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    cap_called = []
    files_called = []

    async def fake_ingest_capsule(*, capsule_dir, finalize, **kw):
        cap_called.append(capsule_dir)
        assert finalize is False
        return {"added_chunks": 3, "files": 1}

    async def fake_ingest_files(*, kb_name, files, app_state, registry, job_id):
        files_called.append(list(files))
        return {"added_chunks": 2, "files": 1}

    registry = AsyncMock()

    with patch.object(local_docs_mod, "ingest_capsule",
                      side_effect=fake_ingest_capsule), \
         patch.object(local_docs_mod, "_ingest_files",
                      side_effect=fake_ingest_files):
        result = await local_docs_mod.ingest_local_documents(
            kb_name="kb1", paths=[cap, pdf], app_state=None,
            registry=registry, job_id="j1",
        )
    assert cap_called == [cap]
    assert files_called and pdf in files_called[0]
    assert result == {"added_chunks": 5, "files": 2}
