"""ChunkMetadata gains ASB-aligned provenance fields (all optional, additive)."""

from __future__ import annotations

import pytest

from perspicacite.models.documents import ChunkMetadata


def test_defaults_are_safe():
    cm = ChunkMetadata(paper_id="p", chunk_index=0)
    assert cm.source_section is None
    assert cm.page is None
    assert cm.char_span is None
    assert cm.figure_refs == []
    assert cm.table_refs == []
    assert cm.resource_refs == []
    assert cm.parent_paper_id is None
    assert cm.is_external is False


def test_round_trip_with_provenance():
    cm = ChunkMetadata(
        paper_id="p", chunk_index=0,
        source_section="methods",
        page=4,
        char_span=(120, 240),
        figure_refs=["pdf_p3_i02"],
        resource_refs=["github:foo/bar"],
        parent_paper_id="doi:10.1234/parent",
        is_external=True,
    )
    dumped = cm.model_dump()
    assert dumped["source_section"] == "methods"
    # char_span may serialize as tuple or list depending on pydantic config
    assert tuple(dumped["char_span"]) == (120, 240)
    cm2 = ChunkMetadata(**dumped)
    assert cm2.figure_refs == ["pdf_p3_i02"]
    assert cm2.is_external is True


def test_existing_chunks_unaffected():
    """Phase 3 chunks (no new fields set) still build cleanly with defaults."""
    cm = ChunkMetadata(
        paper_id="p", chunk_index=0,
        content_type="markdown",
        language=None,
        source_file_path="/tmp/x.md",
    )
    assert cm.figure_refs == []
    assert cm.is_external is False
