"""Tests for GitHubConfig, BundlesConfig, and SearchFilters.source_skill."""
from __future__ import annotations

from perspicacite.config.schema import Config
from perspicacite.models.search import SearchFilters


def test_github_config_defaults():
    cfg = Config()
    assert cfg.github.token_env_var == "GITHUB_TOKEN"
    assert cfg.github.cache_max_mb == 2048


def test_bundles_config_defaults():
    cfg = Config()
    assert cfg.bundles.default_kb_name_template == "{name}"


def test_search_filters_source_skill_default_none():
    f = SearchFilters()
    assert f.source_skill is None
    assert f.is_empty()


def test_search_filters_source_skill_not_empty():
    f = SearchFilters(source_skill="my-skill")
    assert not f.is_empty()
