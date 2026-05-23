"""After the 2026-05-15 Scriptorium-integration audit, the MCP
server must serialize PaperSource as its JSON-friendly .value
('scilex', 'openalex', ...) — not Python's enum repr
('PaperSource.SCILEX'). Downstream clients depend on the lowercase
value to dispatch."""
import inspect

from perspicacite.mcp import server


def test_mcp_server_does_not_emit_enum_repr():
    src = inspect.getsource(server)
    # The repr-style serialization is the bug:
    assert "str(p.source)" not in src, (
        "mcp/server.py must not serialize PaperSource via str() — use .value"
    )
    assert "str(paper.source)" not in src, (
        "mcp/server.py must not serialize PaperSource via str() — use .value"
    )


def test_paper_source_value_is_lowercase_snake():
    """Sanity: the enum .value is the JSON-friendly form."""
    from perspicacite.models.papers import PaperSource
    assert PaperSource.SCILEX.value == "scilex"
    assert PaperSource.OPENALEX.value == "openalex"
