from __future__ import annotations

from perspicacite.models.papers import Paper, PaperSource
from perspicacite.pipeline.chunking_code import _chunk_python_ast

SRC = '''\
class C:
    """C class."""

    @classmethod
    def from_dict(cls, d):
        """Build from dict."""
        return cls()

    @staticmethod
    def helper(x):
        """Pure helper."""
        return x + 1

    @property
    def name(self):
        """Computed name."""
        return "c"

    def plain(self):
        """Regular method."""
        return 0
'''

# Pad inside the class so its source segment crosses the 1500-char gate that
# triggers method-level sub-chunking. Inject padded methods at the bottom.
SRC = SRC + "    # padding lines below\n" + "\n".join(
    f"    def _pad_{i}(self):\n        return {i}" for i in range(60)
) + "\n"


def _paper() -> Paper:
    return Paper(id="p1", title="t", source=PaperSource.BIBTEX)


def test_decorator_kinds_are_recorded():
    chunks = _chunk_python_ast(
        SRC, _paper(), file_path="c.py", chunk_size=4000, chunk_overlap=200,
    )
    by_name = {c.metadata.symbol_name: c.metadata.symbol_kind
               for c in chunks if c.metadata.symbol_kind != "class"}
    assert by_name["from_dict"] == "classmethod"
    assert by_name["helper"] == "staticmethod"
    assert by_name["name"] == "property"
    assert by_name["plain"] == "method"
