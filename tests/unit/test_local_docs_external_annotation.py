"""Cycle C: ingest_local_documents annotates chunks with parent_paper_id and
is_external=True when external_metadata is provided, and pre-processes
.ipynb files via strip_notebook_outputs."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.integrations import local_docs as local_docs_mod


def _app_state():
    s = MagicMock()
    kb = MagicMock()
    kb.collection_name = "col1"
    kb.chunk_count = 0
    kb.name = "kb1"
    s.session_store.get_kb_metadata = AsyncMock(return_value=kb)
    s.session_store.save_kb_metadata = AsyncMock()
    s.embedding_provider.embed = AsyncMock(
        side_effect=lambda texts: [[0.0, 0.1, 0.2] for _ in texts]
    )
    s.vector_store.add_chunks = AsyncMock()
    s.config.knowledge_base.chunk_size = 500
    s.config.knowledge_base.chunk_overlap = 50
    s.config.capsule.auto_build_on_ingest = False
    s.pdf_parser = None
    return s


def _registry():
    r = MagicMock()
    r.publish = AsyncMock()
    r.finish = AsyncMock()
    r.fail = AsyncMock()
    return r


@pytest.mark.asyncio
async def test_external_metadata_annotates_chunks(tmp_path):
    md_file = tmp_path / "README.md"
    md_file.write_text("# Project README\n\nLong content " * 60)

    app_state = _app_state()
    registry = _registry()

    await local_docs_mod.ingest_local_documents(
        kb_name="kb1",
        paths=[md_file],
        app_state=app_state,
        registry=registry,
        job_id="j1",
        recursive=False,
        external_metadata={
            "parent_paper_id": "doi:10.1/x",
            "resource_id": "github:owner/repo",
        },
    )

    calls = app_state.vector_store.add_chunks.call_args_list
    assert calls, "no chunks written"
    chunks = [c for call in calls for c in call.args[1]]
    assert chunks
    for c in chunks:
        assert c.metadata.is_external is True
        assert c.metadata.parent_paper_id == "doi:10.1/x"
        assert "github:owner/repo" in (c.metadata.resource_refs or [])


@pytest.mark.asyncio
async def test_no_external_metadata_leaves_chunks_unflagged(tmp_path):
    md_file = tmp_path / "doc.md"
    md_file.write_text("regular content " * 80)

    app_state = _app_state()
    registry = _registry()

    await local_docs_mod.ingest_local_documents(
        kb_name="kb1",
        paths=[md_file],
        app_state=app_state,
        registry=registry,
        job_id="j1",
        recursive=False,
    )
    chunks = [
        c for call in app_state.vector_store.add_chunks.call_args_list
        for c in call.args[1]
    ]
    for c in chunks:
        assert c.metadata.is_external is False
        assert c.metadata.parent_paper_id is None


@pytest.mark.asyncio
async def test_ipynb_outputs_stripped_before_chunking(tmp_path):
    """A notebook with bulky outputs has them stripped before reaching the
    chunker; downstream chunks should not contain the noisy payload."""
    nb_path = tmp_path / "demo.ipynb"
    big_blob = "X" * 5000
    nb = {
        "cells": [
            {
                "cell_type": "code",
                "source": ["x = 1\n"],
                "execution_count": 1,
                "outputs": [
                    {"output_type": "display_data",
                     "data": {"image/png": big_blob}},
                ],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    nb_path.write_text(json.dumps(nb))

    app_state = _app_state()
    registry = _registry()

    await local_docs_mod.ingest_local_documents(
        kb_name="kb1",
        paths=[nb_path],
        app_state=app_state,
        registry=registry,
        job_id="j1",
        recursive=False,
        external_metadata={"parent_paper_id": "doi:10.1/x"},
    )
    chunks = [
        c for call in app_state.vector_store.add_chunks.call_args_list
        for c in call.args[1]
    ]
    assert chunks
    # No chunk should carry the giant blob payload after stripping.
    for c in chunks:
        assert big_blob not in c.text
