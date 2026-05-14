# tests/unit/test_embedding_factory_caching.py
"""Factory-level test: caching is opt-in via parameters (Wave 2.2)."""
from pathlib import Path

from perspicacite.llm.embeddings import (
    CachedEmbeddingProvider,
    create_embedding_provider,
    SentenceTransformerEmbeddingProvider,
)


def test_factory_wraps_in_cached_provider_when_cache_path_given(tmp_path: Path):
    p = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=True,
        cache_path=tmp_path / "embed.db",
        cache_ttl_days=0,
    )
    assert isinstance(p, CachedEmbeddingProvider)
    assert isinstance(p.inner, SentenceTransformerEmbeddingProvider)
    assert p.model_name == "all-MiniLM-L6-v2"


def test_factory_skips_wrapper_when_cache_disabled(tmp_path: Path):
    p = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
    )
    assert not isinstance(p, CachedEmbeddingProvider)


def test_factory_backwards_compatible_no_cache_params(tmp_path: Path):
    """Existing call sites that don't pass cache params still work."""
    p = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
    )
    # Default: no cache (so callers without cache wiring aren't affected).
    assert not isinstance(p, CachedEmbeddingProvider)
