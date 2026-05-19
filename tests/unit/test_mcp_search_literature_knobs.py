"""Tests for the ``databases`` kwarg on the search_literature MCP tool.

The MCP layer plumbs the ``databases`` filter through the
``DomainAwareAggregator`` so the multi-provider fan-out (europepmc, ads,
pubchem, inspire, google_scholar, scilex, ...) is preserved. These tests
patch the aggregator's ``search`` method to capture what the MCP layer
forwards.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


def _state():
    s = MagicMock()
    s.session_store = MagicMock()
    s.config = MagicMock()
    return s


def _stub_aggregator(capture: dict) -> MagicMock:
    """Return an aggregator stub whose ``search`` records its kwargs."""
    async def _fake_search(*args, **kwargs):
        capture["databases"] = kwargs.get("databases")
        capture["apis"] = kwargs.get("apis")
        return []

    agg = MagicMock()
    agg.available = True
    agg.search = AsyncMock(side_effect=_fake_search)
    agg.last_errors_by_database = {}
    agg._providers = []
    return agg


@pytest.mark.asyncio
async def test_databases_kwarg_restricts_providers():
    captured: dict = {}
    agg = _stub_aggregator(captured)

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5, databases=["arxiv", "crossref"]
        )

    assert captured["databases"] == ["arxiv", "crossref"]
    payload = json.loads(raw)
    # Tolerate either success or empty-result shapes
    assert payload.get("success", True) is not False


@pytest.mark.asyncio
async def test_databases_unknown_entries_dropped():
    captured: dict = {}
    agg = _stub_aggregator(captured)

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        await mcp_server.search_literature(
            query="x", max_results=5,
            databases=["arxiv", "nonsense_db", "pubmed"],
        )

    # Unknown is silently dropped; valid ones flow through.
    assert "arxiv" in captured["databases"]
    assert "pubmed" in captured["databases"]
    assert "nonsense_db" not in captured["databases"]


@pytest.mark.asyncio
async def test_databases_default_passes_none():
    captured: dict = {"databases": "sentinel"}
    agg = _stub_aggregator(captured)

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        await mcp_server.search_literature(query="x", max_results=5)

    assert captured["databases"] is None


@pytest.mark.asyncio
async def test_databases_empty_after_filter_falls_back_to_default():
    captured: dict = {}
    agg = _stub_aggregator(captured)

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.domain_aggregator.build_aggregator",
        return_value=agg,
    ):
        await mcp_server.search_literature(
            query="x", max_results=5,
            databases=["all_unknown_1", "all_unknown_2"],
        )

    # All entries dropped → falls back to None (server defaults).
    assert captured["databases"] is None
