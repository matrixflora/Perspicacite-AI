"""When show_code is True, RAG modes attach code_excerpts to RAGResponse.

This test verifies the integration shape by exercising collect_code_excerpts
directly against the same model contract; it doesn't run a full LLM round-trip."""
from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.rag import RAGMode, RAGResponse
from perspicacite.rag.code_excerpts import collect_code_excerpts


def _code_chunk():
    return DocumentChunk(
        id="github:o/r@abc:f.py_0",
        text="def fit(): pass",
        metadata=ChunkMetadata(
            paper_id="github:o/r@abc:f.py", chunk_index=0,
            content_type="code", language="python",
            source_file_path="f.py",
            symbol_name="fit", symbol_kind="function",
            start_line=1, end_line=5,
        ),
    )


def test_excerpts_extracted_and_attached_to_response():
    chunks = [_code_chunk()]
    excerpts = collect_code_excerpts(chunks)
    resp = RAGResponse(
        answer="example",
        mode=RAGMode.BASIC,
        code_excerpts=excerpts,
    )
    assert len(resp.code_excerpts) == 1
    assert resp.code_excerpts[0].source_url.startswith("https://github.com/")
