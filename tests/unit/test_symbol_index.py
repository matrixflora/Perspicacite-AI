from pathlib import Path

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.pipeline.symbol_index import (
    SymbolRecord,
    append_symbols,
    iter_symbols,
    symbols_from_chunks,
)


def _make_chunk(symbol_name, kind, start, end, **kwargs):
    md = ChunkMetadata(
        paper_id="github:owner/repo@abc:file.py",
        chunk_index=0,
        content_type="code",
        language="python",
        symbol_name=symbol_name,
        symbol_kind=kind,
        start_line=start,
        end_line=end,
        source_file_path=kwargs.get("file_path", "file.py"),
        docstring=kwargs.get("docstring"),
        imports=kwargs.get("imports", []),
    )
    return DocumentChunk(id=f"c_{symbol_name}", text="def x(): pass", metadata=md)


def test_symbols_from_chunks_extracts_code_only():
    code = _make_chunk("fit", "function", 1, 10)
    text_md = ChunkMetadata(paper_id="p", chunk_index=1, content_type="text")
    text_chunk = DocumentChunk(id="t", text="hello", metadata=text_md)
    syms = symbols_from_chunks([code, text_chunk])
    assert len(syms) == 1
    assert syms[0].symbol_name == "fit"


def test_append_and_iter_round_trip(tmp_path: Path):
    sym = SymbolRecord(
        paper_id="p1",
        symbol_name="fit",
        symbol_kind="function",
        file_path="file.py",
        start_line=1,
        end_line=10,
        signature="def fit()",
        docstring=None,
        imports=["numpy"],
    )
    append_symbols(tmp_path, "p1", [sym])
    out = list(iter_symbols(tmp_path))
    assert len(out) == 1
    assert out[0].symbol_name == "fit"
    assert out[0].imports == ["numpy"]


def test_iter_symbols_name_glob_filter(tmp_path: Path):
    a = SymbolRecord(paper_id="p", symbol_name="fit_transform", symbol_kind="function",
                     file_path="a.py", start_line=1, end_line=2, signature="def fit_transform()",
                     docstring=None, imports=[])
    b = SymbolRecord(paper_id="p", symbol_name="predict", symbol_kind="function",
                     file_path="a.py", start_line=10, end_line=11, signature="def predict()",
                     docstring=None, imports=[])
    append_symbols(tmp_path, "p", [a, b])
    out = list(iter_symbols(tmp_path, name_glob="fit_*"))
    assert [s.symbol_name for s in out] == ["fit_transform"]


def test_append_is_append_only(tmp_path: Path):
    s1 = SymbolRecord(paper_id="p1", symbol_name="a", symbol_kind="function",
                      file_path="x.py", start_line=1, end_line=2, signature="def a()",
                      docstring=None, imports=[])
    s2 = SymbolRecord(paper_id="p2", symbol_name="b", symbol_kind="function",
                      file_path="y.py", start_line=1, end_line=2, signature="def b()",
                      docstring=None, imports=[])
    append_symbols(tmp_path, "p1", [s1])
    append_symbols(tmp_path, "p2", [s2])
    out = list(iter_symbols(tmp_path))
    assert {s.paper_id for s in out} == {"p1", "p2"}
