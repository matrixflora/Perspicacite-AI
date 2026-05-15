# tests/unit/test_flatten_paper_results.py
"""Verify the paper-results → DocumentChunks flattener that the
sub-project C mode hooks use to extract code excerpts and figure refs."""
from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.utils import flatten_paper_results_to_chunks


def _md(paper_id="p1", idx=0, content_type="text", **kwargs):
    return ChunkMetadata(
        paper_id=paper_id, chunk_index=idx, content_type=content_type, **kwargs,
    )


def test_flattens_inner_chunks_into_documentchunks():
    paper_results = [
        {
            "paper_id": "p1", "title": "T1", "chunks": [
                {"text": "abc", "paper_id": "p1", "chunk_index": 0, "metadata": _md("p1", 0)},
                {"text": "def f():", "paper_id": "p1", "chunk_index": 1,
                 "metadata": _md("p1", 1, content_type="code", language="python",
                                 symbol_name="f", symbol_kind="function",
                                 start_line=1, end_line=5,
                                 source_file_path="f.py")},
            ],
            "full_text": "abc def f():",
        },
    ]
    chunks = flatten_paper_results_to_chunks(paper_results)
    assert len(chunks) == 2
    assert all(isinstance(c, DocumentChunk) for c in chunks)
    assert chunks[0].text == "abc"
    assert chunks[1].metadata.symbol_name == "f"


def test_skips_dicts_without_metadata():
    paper_results = [{
        "paper_id": "p", "chunks": [
            {"text": "ignored", "paper_id": "p", "chunk_index": 0},  # no metadata
        ],
    }]
    assert flatten_paper_results_to_chunks(paper_results) == []


def test_empty_input_returns_empty():
    assert flatten_paper_results_to_chunks([]) == []
    assert flatten_paper_results_to_chunks(None) == []  # tolerate None


def test_multiple_papers_preserved_in_order():
    p1_chunks = [
        {"text": f"p1_t{i}", "paper_id": "p1", "chunk_index": i, "metadata": _md("p1", i)}
        for i in range(3)
    ]
    p2_chunks = [
        {"text": f"p2_t{i}", "paper_id": "p2", "chunk_index": i, "metadata": _md("p2", i)}
        for i in range(2)
    ]
    paper_results = [
        {"paper_id": "p1", "chunks": p1_chunks},
        {"paper_id": "p2", "chunks": p2_chunks},
    ]
    out = flatten_paper_results_to_chunks(paper_results)
    assert [c.text for c in out] == ["p1_t0", "p1_t1", "p1_t2", "p2_t0", "p2_t1"]


def test_skips_legacy_metadata_shape():
    """If metadata is a dict (legacy), skip silently."""
    paper_results = [{
        "paper_id": "p", "chunks": [
            {"text": "x", "paper_id": "p", "chunk_index": 0,
             "metadata": {"paper_id": "p", "chunk_index": 0}},  # dict, not ChunkMetadata
        ],
    }]
    # Should be empty (we don't reconstruct from raw dicts to avoid
    # silent field-name drift).
    assert flatten_paper_results_to_chunks(paper_results) == []
