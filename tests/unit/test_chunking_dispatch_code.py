# tests/unit/test_chunking_dispatch_code.py
import pytest

from perspicacite.config.schema import KnowledgeBaseConfig
from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_dispatch import chunk_document


def _paper():
    return Paper(id="github:o/r@abc:f.py", title="t", abstract="", source=PaperSource.BIBTEX)


@pytest.mark.asyncio
async def test_auto_routes_python_to_ast():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    src = "def foo():\n    return 1\n\nclass Bar:\n    pass\n"
    chunks = await chunk_document(src, _paper(), content_type="code",
                                  language="python", config=cfg)
    kinds = sorted({c.metadata.symbol_kind for c in chunks})
    assert kinds == ["class", "function"]


@pytest.mark.asyncio
async def test_splitter_preserves_today_behaviour():
    cfg = KnowledgeBaseConfig(code_chunking="splitter")
    src = "def foo():\n    return 1\n"
    chunks = await chunk_document(src, _paper(), content_type="code",
                                  language="python", config=cfg)
    # Splitter path does not set symbol_name.
    assert all(c.metadata.symbol_name is None for c in chunks)


@pytest.mark.asyncio
async def test_r_routes_to_regex():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    src = "foo <- function(x) x + 1\n"
    paper = Paper(id="github:o/r@abc:f.R", title="t", abstract="",
                  source=PaperSource.BIBTEX)
    chunks = await chunk_document(src, paper, content_type="code",
                                  language="r", config=cfg)
    assert [c.metadata.symbol_name for c in chunks] == ["foo"]


@pytest.mark.asyncio
async def test_ipynb_routes_to_notebook():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    paper = Paper(id="github:o/r@abc:nb.ipynb", title="t", abstract="",
                  source=PaperSource.BIBTEX)
    nb = (
        '{"cells":[{"cell_type":"code","source":["def x():\\n","    return 1\\n"]}],'
        '"metadata":{},"nbformat":4,"nbformat_minor":5}'
    )
    chunks = await chunk_document(nb, paper, content_type="code",
                                  language="ipynb", config=cfg)
    assert any(c.metadata.symbol_name == "x" for c in chunks)


@pytest.mark.asyncio
async def test_text_content_type_unchanged():
    cfg = KnowledgeBaseConfig(code_chunking="auto")
    paper = Paper(id="p", title="t", abstract="", source=PaperSource.BIBTEX)
    # Use short text (well under one chunk) to avoid the overlap edge-case in
    # _chunk_by_tokens when total text < char_per_chunk + overlap_chars.
    chunks = await chunk_document("just plain text", paper,
                                  content_type="text", language=None, config=cfg)
    # Plain text never touches the code chunker.
    assert all(c.metadata.symbol_name is None for c in chunks)
