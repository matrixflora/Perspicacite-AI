from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_python_ast

# Build a class large enough to exceed 1500 chars in its source segment so
# method-level sub-chunking kicks in.
_PADDING = "\n".join(f"        a_{i} = {i}" for i in range(60))
LARGE_CLASS = f'''\
"""Module docstring."""
import os


class BigThing:
    """A class with many methods."""

    def __init__(self, x: int) -> None:
        """Init."""
        self.x = x
        self.y = x * 2

    def compute(self, factor: int) -> int:
        """Multiply x by a factor and y by 3.

        Long body to ensure class exceeds the 1500-char threshold so
        method-level sub-chunking kicks in.
        """
{_PADDING}
        a = self.x * factor
        b = self.y * 3
        c = a + b
        d = c - factor
        e = d ** 2
        return e

    def explain(self) -> str:
        """Return a description."""
        parts = [f"x={{self.x}}", f"y={{self.y}}"]
        return ", ".join(parts)
'''


def _paper() -> Paper:
    return Paper(id="p1", title="t", source=PaperSource.BIBTEX)


def test_large_class_emits_method_chunks_plus_class_chunk():
    chunks = _chunk_python_ast(
        LARGE_CLASS, _paper(),
        file_path="src/bigthing.py",
        chunk_size=4000, chunk_overlap=200,
    )
    kinds = [c.metadata.symbol_kind for c in chunks]
    # One class-level chunk plus N method-level chunks.
    assert "class" in kinds
    assert kinds.count("method") >= 2
    method_chunks = [c for c in chunks if c.metadata.symbol_kind == "method"]
    for mc in method_chunks:
        assert mc.metadata.parent_class == "BigThing"
        assert mc.metadata.symbol_name in {"__init__", "compute", "explain"}


def test_small_class_only_emits_class_chunk():
    src = (
        "class Tiny:\n"
        '    """Short."""\n'
        "    def m(self):\n"
        "        return 1\n"
    )
    chunks = _chunk_python_ast(
        src, _paper(),
        file_path="src/tiny.py",
        chunk_size=4000, chunk_overlap=200,
    )
    kinds = [c.metadata.symbol_kind for c in chunks]
    assert kinds == ["class"]
