from __future__ import annotations

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.rag.code_excerpts import (
    build_github_source_url,
    collect_code_excerpts,
)


def _code_chunk(paper_id, idx, name, *, language="python", start=1, end=10,
                file_path="f.py", text="def fit(): pass", kind="function"):
    md = ChunkMetadata(
        paper_id=paper_id, chunk_index=idx, content_type="code",
        language=language, source_file_path=file_path,
        symbol_name=name, symbol_kind=kind,
        start_line=start, end_line=end,
    )
    return DocumentChunk(id=f"{paper_id}_{idx}", text=text, metadata=md)


def _text_chunk(paper_id):
    md = ChunkMetadata(paper_id=paper_id, chunk_index=0, content_type="text")
    return DocumentChunk(id=f"{paper_id}_t", text="hello", metadata=md)


def test_skips_non_code_chunks():
    chunks = [_text_chunk("p1"), _code_chunk("p1", 1, "fit")]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1
    assert excerpts[0].symbol_name == "fit"


def test_github_url_for_github_paper_id():
    chunks = [_code_chunk("github:tiangolo/typer@deadbeef:typer/main.py",
                          0, "run", file_path="typer/main.py", start=42, end=58)]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1
    assert excerpts[0].source_url == (
        "https://github.com/tiangolo/typer/blob/deadbeef/typer/main.py#L42-L58"
    )


def test_url_falls_back_to_paper_id_when_not_github():
    chunks = [_code_chunk("zotero:abc123", 0, "fit")]
    excerpts = collect_code_excerpts(chunks)
    assert excerpts[0].source_url  # non-empty
    assert "zotero:abc123" in excerpts[0].source_url or excerpts[0].source_url == "zotero:abc123"


def test_dedup_by_paper_file_start_end():
    chunks = [
        _code_chunk("p1", 0, "fit", start=1, end=10),
        _code_chunk("p1", 1, "fit", start=1, end=10),
    ]
    excerpts = collect_code_excerpts(chunks)
    assert len(excerpts) == 1


def test_module_chunk_has_no_symbol_name():
    md = ChunkMetadata(
        paper_id="github:o/r@abc:f.py", chunk_index=0, content_type="code",
        language="python", source_file_path="f.py",
        symbol_name="f.py", symbol_kind="module",
        start_line=1, end_line=50,
    )
    chunk = DocumentChunk(id="x", text="...", metadata=md)
    excerpts = collect_code_excerpts([chunk])
    assert len(excerpts) == 1
    assert excerpts[0].symbol_kind == "module"
    # Module chunks where symbol_name equals file_path → surface as None.
    assert excerpts[0].symbol_name is None


def test_build_github_source_url_directly():
    url = build_github_source_url(
        paper_id="github:tiangolo/typer@deadbeef:typer/main.py",
        start_line=42, end_line=58,
    )
    assert url == "https://github.com/tiangolo/typer/blob/deadbeef/typer/main.py#L42-L58"


def test_build_github_source_url_returns_none_for_non_github():
    url = build_github_source_url(
        paper_id="zotero:abc", start_line=1, end_line=2,
    )
    assert url is None
