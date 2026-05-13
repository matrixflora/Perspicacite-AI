"""PaperSource.LOCAL and ChunkMetadata local-doc fields."""

from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata
from perspicacite.models.papers import PaperSource


def test_paper_source_local_exists():
    assert PaperSource.LOCAL == "local"


def test_chunk_metadata_local_fields():
    md = ChunkMetadata(
        paper_id="local:abc",
        chunk_index=0,
        source=PaperSource.LOCAL,
        content_type="markdown",
        language=None,
        heading_path=["Intro", "Setup"],
        source_file_path="/abs/path.md",
    )
    assert md.content_type == "markdown"
    assert md.heading_path == ["Intro", "Setup"]
    assert md.source_file_path == "/abs/path.md"


def test_chunk_metadata_back_compat_without_new_fields():
    md = ChunkMetadata(paper_id="p1", chunk_index=0)
    assert md.content_type is None
    assert md.language is None
    assert md.heading_path is None
    assert md.source_file_path is None
