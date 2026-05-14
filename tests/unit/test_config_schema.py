"""Tests for config schema defaults."""
from __future__ import annotations

from perspicacite.config.schema import KnowledgeBaseConfig


def test_mcp_resource_max_events_defaults_to_1000():
    cfg = KnowledgeBaseConfig()
    assert cfg.mcp_resource_max_events == 1000
