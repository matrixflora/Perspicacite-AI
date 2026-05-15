# tests/unit/test_chunking_code_notebook.py
import json

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_notebook


def _paper():
    return Paper(id="github:o/r@abc:nb.ipynb", title="t", abstract="", source=PaperSource.BIBTEX)


def _nb(cells):
    return json.dumps({"cells": cells, "metadata": {}, "nbformat": 4, "nbformat_minor": 5})


def test_one_code_cell_one_chunk():
    src = _nb([
        {"cell_type": "markdown", "source": ["# Title\n"]},
        {"cell_type": "code", "source": ["x = 1\n", "y = 2\n"], "outputs": [{"output_type": "stream"}]},
    ])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "cell"
    assert chunks[0].metadata.symbol_name == "nb.ipynb::cell_1"
    # Cell outputs must be stripped from the chunk text.
    assert "output_type" not in chunks[0].text


def test_cell_with_function_def_yields_function_chunk():
    src = _nb([
        {"cell_type": "code", "source": ["def hello():\n", "    return 1\n"]},
    ])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "function"
    assert chunks[0].metadata.symbol_name == "hello"


def test_malformed_json_falls_back_to_module():
    chunks = _chunk_notebook("not json {", _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"


def test_no_code_cells_yields_empty_module():
    src = _nb([{"cell_type": "markdown", "source": ["# x"]}])
    chunks = _chunk_notebook(src, _paper(), file_path="nb.ipynb")
    assert len(chunks) == 1
    assert chunks[0].metadata.symbol_kind == "module"
