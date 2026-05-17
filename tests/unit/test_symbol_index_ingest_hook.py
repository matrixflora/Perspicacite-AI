# tests/unit/test_symbol_index_ingest_hook.py
from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.pipeline.symbol_index import iter_symbols, write_chunks_symbols


def _code_chunk(paper_id: str, idx: int, name: str) -> DocumentChunk:
    md = ChunkMetadata(
        paper_id=paper_id,
        chunk_index=idx,
        content_type="code",
        language="python",
        source_file_path="f.py",
        symbol_name=name,
        symbol_kind="function",
        start_line=1,
        end_line=5,
        imports=["numpy"],
    )
    return DocumentChunk(id=f"{paper_id}_{idx}", text=f"def {name}(): pass\n", metadata=md)


def test_writes_one_record_per_code_chunk(tmp_path: Path):
    chunks = [_code_chunk("p1", 0, "fit"), _code_chunk("p1", 1, "predict")]
    n = write_chunks_symbols(kb_dir=tmp_path, chunks=chunks)
    assert n == 2
    out = list(iter_symbols(tmp_path))
    assert {s.symbol_name for s in out} == {"fit", "predict"}


def test_skips_non_code(tmp_path: Path):
    md = ChunkMetadata(paper_id="p1", chunk_index=0, content_type="text")
    text_chunk = DocumentChunk(id="t", text="hello", metadata=md)
    n = write_chunks_symbols(kb_dir=tmp_path, chunks=[text_chunk])
    assert n == 0
    assert list(iter_symbols(tmp_path)) == []
