# tests/unit/test_config_embedding_cache_fields.py
"""Tests for embedding-cache fields on KnowledgeBaseConfig (Wave 2.2)."""
from pathlib import Path

from perspicacite.config.schema import KnowledgeBaseConfig


def test_embedding_cache_defaults():
    """Default-on, never-expire. See spec rationale."""
    kb = KnowledgeBaseConfig()
    assert kb.embedding_cache_enabled is True
    assert kb.embedding_cache_path == Path("data/embedding_cache.db")
    assert kb.embedding_cache_ttl_days == 0  # 0 = forever


def test_embedding_cache_disable():
    kb = KnowledgeBaseConfig(embedding_cache_enabled=False)
    assert kb.embedding_cache_enabled is False


def test_embedding_cache_path_coerces_string():
    kb = KnowledgeBaseConfig(embedding_cache_path="custom/embed.db")  # type: ignore[arg-type]
    assert kb.embedding_cache_path == Path("custom/embed.db")


def test_embedding_cache_ttl_accepts_zero_and_positive():
    KnowledgeBaseConfig(embedding_cache_ttl_days=0)
    KnowledgeBaseConfig(embedding_cache_ttl_days=30)
