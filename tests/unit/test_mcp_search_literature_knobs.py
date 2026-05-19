"""Tests for the new ``databases`` kwarg on search_literature MCP tool."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


def _state():
    s = MagicMock()
    s.session_store = MagicMock()
    s.config = MagicMock()
    return s


async def test_databases_kwarg_restricts_providers():
    captured = {}

    async def _fake_search(*args, **kwargs):
        captured["databases"] = kwargs.get("databases")
        return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter.search_with_warnings",
        new=_fake_search,
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5, databases=["arxiv", "crossref"]
        )

    assert captured["databases"] == ["arxiv", "crossref"]
    payload = json.loads(raw)
    # Tolerate either success or empty-result shapes
    assert payload.get("success", True) is not False


async def test_databases_unknown_entries_dropped():
    captured = {}

    async def _fake_search(*args, **kwargs):
        captured["databases"] = kwargs.get("databases")
        return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter.search_with_warnings",
        new=_fake_search,
    ):
        await mcp_server.search_literature(
            query="x", max_results=5,
            databases=["arxiv", "nonsense_db", "pubmed"],
        )

    # Unknown is silently dropped; valid ones flow through.
    assert "arxiv" in captured["databases"]
    assert "pubmed" in captured["databases"]
    assert "nonsense_db" not in captured["databases"]


async def test_databases_default_passes_none():
    captured = {"db": "sentinel"}

    async def _fake_search(*args, **kwargs):
        captured["db"] = kwargs.get("databases")
        return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter.search_with_warnings",
        new=_fake_search,
    ):
        await mcp_server.search_literature(query="x", max_results=5)

    assert captured["db"] is None


async def test_databases_empty_after_filter_falls_back_to_default():
    captured = {}

    async def _fake_search(*args, **kwargs):
        captured["databases"] = kwargs.get("databases")
        return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter.search_with_warnings",
        new=_fake_search,
    ):
        await mcp_server.search_literature(
            query="x", max_results=5,
            databases=["all_unknown_1", "all_unknown_2"],
        )

    # All entries dropped → falls back to None (server defaults)
    assert captured["databases"] is None
