"""Tests for config schema defaults."""
from __future__ import annotations

from pathlib import Path

from perspicacite.config.schema import (
    BundlesConfig,
    Config,
    GitHubConfig,
    KnowledgeBaseConfig,
)


def test_mcp_resource_max_events_defaults_to_1000():
    cfg = KnowledgeBaseConfig()
    assert cfg.mcp_resource_max_events == 1000


def test_github_config_defaults():
    cfg = GitHubConfig()
    assert cfg.token_env_var == "GITHUB_TOKEN"
    assert cfg.cache_dir == Path("data/github_cache")
    assert cfg.cache_max_mb == 2048
    assert cfg.default_branch == "HEAD"
    assert cfg.user_agent == "Perspicacite/2.0"
    assert cfg.api_base == "https://api.github.com"


def test_bundles_config_defaults():
    cfg = BundlesConfig()
    assert cfg.default_kb_name_template == "{name}"
    assert cfg.composite_kb_name_template == "composite-{domain}"


def test_config_has_github_and_bundles():
    cfg = Config()
    assert isinstance(cfg.github, GitHubConfig)
    assert isinstance(cfg.bundles, BundlesConfig)
    # Spot-check that nested defaults are reachable through the parent Config.
    assert cfg.github.token_env_var == "GITHUB_TOKEN"
    assert cfg.bundles.default_kb_name_template == "{name}"
