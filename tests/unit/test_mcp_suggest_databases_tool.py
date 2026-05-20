"""Tests for the ``suggest_databases`` MCP tool."""
from __future__ import annotations

import json

from perspicacite.mcp import server as mcp_server
from perspicacite.search.scilex_adapter import KNOWN_DATABASES


async def test_suggest_databases_recommends_pubmed_for_crispr():
    raw = await mcp_server.suggest_databases(query="CRISPR gene editing in human cells")
    payload = json.loads(raw)

    assert payload["success"] is True
    assert "pubmed" in payload["recommended"]
    assert payload["reasoning"]
    assert set(payload["all_known"]) == set(KNOWN_DATABASES)
