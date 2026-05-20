"""Tests for the MCP usage guide and its tool-coverage drift check."""

import json

from perspicacite.mcp import server as mcp_server
from perspicacite.mcp.server import _TOOL_NAMES
from perspicacite.mcp.usage_guide import build_usage_guide


def test_build_usage_guide_shape():
    guide = build_usage_guide()
    assert isinstance(guide, dict)
    for key in ("capabilities", "decision_rules", "tools", "knob_defaults"):
        assert key in guide, f"missing key: {key}"
    assert isinstance(guide["tools"], list)
    assert len(guide["tools"]) > 0


def test_usage_guide_covers_every_registered_tool():
    """Drift guard: every name in _TOOL_NAMES must be documented."""
    guide = build_usage_guide()
    documented = {t["name"] for t in guide["tools"]}
    registered = set(_TOOL_NAMES)
    missing = registered - documented
    assert not missing, f"undocumented tools: {sorted(missing)}"


def test_each_tool_entry_is_well_formed():
    guide = build_usage_guide()
    for entry in guide["tools"]:
        assert entry.get("name"), f"empty name in entry: {entry}"
        assert entry.get("purpose"), f"empty purpose for {entry.get('name')}"
        assert entry.get("when_to_use"), f"empty when_to_use for {entry.get('name')}"


async def test_get_usage_guide_tool_returns_envelope():
    raw = await mcp_server.get_usage_guide()
    payload = json.loads(raw)
    assert payload["success"] is True
    assert "tools" in payload
