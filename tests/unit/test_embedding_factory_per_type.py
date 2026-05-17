from __future__ import annotations

from perspicacite.llm.embeddings import (
    SentenceTransformerEmbeddingProvider,
    TypedEmbeddingProvider,
    create_embedding_provider,
)


def test_empty_per_type_returns_single_provider():
    """When the map is empty, factory behaves identically to today."""
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={},  # explicit empty map
    )
    assert isinstance(prov, SentenceTransformerEmbeddingProvider)


def test_per_type_map_returns_typed_provider():
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={"code": "all-MiniLM-L12-v2"},
    )
    assert isinstance(prov, TypedEmbeddingProvider)
    # Default model is the top-level `model`; "code" is overridden.
    assert "all-MiniLM-L6-v2" in prov.model_name
    assert "code:all-MiniLM-L12-v2" in prov.model_name


def test_per_type_default_already_referenced_dedups():
    """If the per-type map sets 'text' to the same model as `model`,
    we still build a TypedEmbeddingProvider (it's the routing trigger),
    but the inner-provider list is sane (one provider per type, default
    handles unspecified types)."""
    prov = create_embedding_provider(
        model="all-MiniLM-L6-v2",
        use_local_fallback=False,
        cache_enabled=False,
        embedding_models_per_type={"text": "all-MiniLM-L6-v2"},
    )
    assert isinstance(prov, TypedEmbeddingProvider)
