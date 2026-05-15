# tests/unit/test_chunking_code_ast.py
from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_python_ast


def _paper():
    return Paper(
        id="github:o/r@abc:f.py",
        title="t",
        abstract="",
        source=PaperSource.BIBTEX,
    )


def test_single_function_yields_one_function_chunk():
    src = (
        "import numpy\n"
        "import scipy.stats as st\n\n"
        "def fit(x):\n"
        '    """Fit a model."""\n'
        "    return x\n"
    )
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    md = chunks[0].metadata
    assert md.symbol_name == "fit"
    assert md.symbol_kind == "function"
    assert md.start_line == 3
    assert md.end_line == 5
    assert md.docstring == "Fit a model."
    assert "numpy" in md.imports
    assert "scipy" in md.imports
    assert md.language == "python"
    assert md.content_type == "code"


def test_async_function_marked_function():
    src = "async def go():\n    pass\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "function"
    assert chunks[0].metadata.symbol_name == "go"


def test_class_yields_single_class_chunk_not_methods():
    src = (
        "class Pipeline:\n"
        "    def step_a(self):\n"
        "        pass\n"
        "    def step_b(self):\n"
        "        pass\n"
    )
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    # ASB convention: top-level only — one class chunk, not two methods.
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "class"
    assert chunks[0].metadata.symbol_name == "Pipeline"


def test_syntax_error_falls_back_to_module():
    src = "def f(:\n  bad\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_no_top_level_defs_falls_back_to_module():
    src = "x = 1\ny = 2\n"
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_docstring_truncated_to_500_chars():
    long = "x " * 400  # 800 chars
    src = f'def f():\n    """{long}"""\n    pass\n'
    chunks = _chunk_python_ast(src, _paper(), file_path="f.py", chunk_size=1000, chunk_overlap=200)
    assert chunks[0].metadata.docstring is not None
    assert len(chunks[0].metadata.docstring) <= 500
