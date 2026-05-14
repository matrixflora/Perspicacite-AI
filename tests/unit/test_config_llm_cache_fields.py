"""Tests for the cache-related LLMConfig fields (Wave 2.1)."""
from pathlib import Path

from perspicacite.config.schema import LLMConfig


def test_llm_cache_defaults_are_sensible():
    """Cache should be enabled by default with 24h TTL.

    Rationale: the dev-iteration win is huge and the worst-case
    failure (stale response) is easy to spot and bypass per-call.
    Default-on prevents users from forgetting to enable it and missing
    the speedup.
    """
    cfg = LLMConfig()
    assert cfg.cache_enabled is True
    assert cfg.cache_path == Path("data/llm_cache.db")
    assert cfg.cache_ttl_hours == 24


def test_llm_cache_can_be_disabled():
    cfg = LLMConfig(cache_enabled=False)
    assert cfg.cache_enabled is False


def test_llm_cache_ttl_zero_means_forever():
    """TTL=0 is the documented sentinel for 'never expire'."""
    cfg = LLMConfig(cache_ttl_hours=0)
    assert cfg.cache_ttl_hours == 0


def test_llm_cache_path_accepts_string_and_path():
    """Pydantic should coerce a YAML string into Path."""
    cfg = LLMConfig(cache_path="some/other/path.db")  # type: ignore[arg-type]
    assert cfg.cache_path == Path("some/other/path.db")
