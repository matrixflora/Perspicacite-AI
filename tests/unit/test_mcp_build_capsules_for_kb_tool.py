"""MCP bulk build tool surface."""

from __future__ import annotations

import json
import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_get_info_lists_fifteen_tools():
    raw = await mcp_server.get_info()
    info = json.loads(raw)
    assert info["tool_count"] >= 15
    assert "build_capsules_for_kb" in info["tools"]
