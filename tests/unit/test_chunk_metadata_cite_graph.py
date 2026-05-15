import pytest
from pydantic import ValidationError
from perspicacite.models.documents import ChunkMetadata


def test_source_via_default_is_none_or_bundle():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.source_via in (None, "bundle")


def test_source_via_accepts_cite_graph_values():
    md = ChunkMetadata(paper_id="p", chunk_index=0, source_via="cite_graph")
    assert md.source_via == "cite_graph"


def test_source_via_accepts_cite_graph_script():
    md = ChunkMetadata(paper_id="p", chunk_index=0, source_via="cite_graph_script")
    assert md.source_via == "cite_graph_script"


def test_invalid_source_via_rejected():
    with pytest.raises(ValidationError):
        ChunkMetadata(paper_id="p", chunk_index=0, source_via="unknown_kind")


def test_cited_tool_default_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.cited_tool is None


def test_cited_tool_round_trip():
    md = ChunkMetadata(paper_id="p", chunk_index=0, cited_tool="openff-evaluator")
    assert md.cited_tool == "openff-evaluator"


def test_discovery_score_default_none():
    md = ChunkMetadata(paper_id="p", chunk_index=0)
    assert md.discovery_score is None


def test_discovery_score_round_trip():
    md = ChunkMetadata(paper_id="p", chunk_index=0, discovery_score=0.73)
    assert md.discovery_score == 0.73
