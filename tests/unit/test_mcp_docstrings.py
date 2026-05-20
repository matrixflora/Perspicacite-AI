"""Docstring-quality guard for the four "thin" MCP passage/extraction tools.

The descriptions exposed to a calling LLM come straight from each tool's
docstring. These assertions keep those docstrings substantive (a hard length
floor) and ensure they include explicit usage guidance ("when to use ..."),
so the model can disambiguate the tool from its nearest alternative.
"""

from __future__ import annotations

import inspect

import pytest

import perspicacite.mcp.server as mcp_server

_THIN_TOOLS = [
    "search_by_passage",
    "get_relevant_passages",
    "extract_parameters_from_passages",
    "extract_failure_modes_from_passages",
]


@pytest.mark.parametrize("name", _THIN_TOOLS)
def test_thin_tool_docstring_is_substantive(name: str) -> None:
    doc = inspect.getdoc(getattr(mcp_server, name))
    assert doc is not None, f"{name} has no docstring"
    assert len(doc) >= 200, f"{name} docstring too short ({len(doc)} chars)"
    assert "use" in doc.lower(), f"{name} docstring lacks usage guidance"
