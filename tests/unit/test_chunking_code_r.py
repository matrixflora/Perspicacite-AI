# tests/unit/test_chunking_code_r.py
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_r_regex


def _paper():
    return Paper(id="github:o/r@abc:f.R", title="t", abstract="", source=PaperSource.BIBTEX)


def test_two_functions_two_chunks():
    src = (
        "library(dplyr)\n\n"
        "foo <- function(x) {\n"
        "  x + 1\n"
        "}\n\n"
        "bar.baz <- function(y, z) {\n"
        "  y * z\n"
        "}\n"
    )
    chunks = _chunk_r_regex(src, _paper(), file_path="f.R")
    names = [c.metadata.symbol_name for c in chunks]
    assert names == ["foo", "bar.baz"]
    assert all(c.metadata.symbol_kind == "function" for c in chunks)
    assert all(c.metadata.content_type == "code" for c in chunks)
    assert all(c.metadata.language == "r" for c in chunks)


def test_no_functions_falls_back_to_module():
    src = "x <- 1\ny <- 2\n"
    chunks = _chunk_r_regex(src, _paper(), file_path="f.R")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"
