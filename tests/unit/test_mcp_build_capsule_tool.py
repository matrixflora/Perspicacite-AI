"""MCP build_capsule tool surface."""

from __future__ import annotations

import json

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_get_info_lists_fourteen_tools():
    raw = await mcp_server.get_info()
    info = json.loads(raw)
    assert info["tool_count"] >= 14
    assert "build_capsule" in info["tools"]
