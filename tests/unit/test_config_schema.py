"""Tests for config schema defaults."""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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


# Validator coverage — must reject misconfigurations early so Tasks
# 2-10 don't crash deep inside ingest with surprising errors.

@pytest.mark.parametrize("bad_value", [0, -1, -2048])
def test_github_config_rejects_non_positive_cache_max_mb(bad_value):
    with pytest.raises(ValidationError):
        GitHubConfig(cache_max_mb=bad_value)


@pytest.mark.parametrize("bad_value", [
    "github.com/api",        # missing scheme
    "ftp://api.github.com",  # wrong scheme
    "",                      # empty
])
def test_github_config_rejects_non_http_api_base(bad_value):
    with pytest.raises(ValidationError):
        GitHubConfig(api_base=bad_value)


@pytest.mark.parametrize("good_value", [
    "https://api.github.com",
    "http://localhost:8080",
    "https://ghe.example.com/api/v3",
])
def test_github_config_accepts_valid_api_base(good_value):
    cfg = GitHubConfig(api_base=good_value)
    assert cfg.api_base == good_value


@pytest.mark.parametrize("field,bad_value", [
    ("token_env_var", ""),
    ("default_branch", ""),
    ("user_agent", ""),
])
def test_github_config_rejects_empty_strings(field, bad_value):
    with pytest.raises(ValidationError):
        GitHubConfig(**{field: bad_value})


def test_bundles_config_rejects_default_template_without_name_placeholder():
    with pytest.raises(ValidationError):
        BundlesConfig(default_kb_name_template="kb-static")


def test_bundles_config_rejects_composite_template_without_domain_placeholder():
    with pytest.raises(ValidationError):
        BundlesConfig(composite_kb_name_template="composite-static")


def test_bundles_config_accepts_custom_valid_templates():
    cfg = BundlesConfig(
        default_kb_name_template="my-{name}-kb",
        composite_kb_name_template="multi-{domain}",
    )
    assert cfg.default_kb_name_template == "my-{name}-kb"
    assert cfg.composite_kb_name_template == "multi-{domain}"
