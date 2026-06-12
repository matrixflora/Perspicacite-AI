"""Regression: an API embedder must not silently fall back to a different-dimension
local model.

The local fallback (all-MiniLM-L6-v2, 384-dim) only ever wraps API models
(text-embedding-3-large, 3072-dim). On any API error it emitted 384-dim vectors that a
3072-dim Chroma collection rejects — poisoning ingests. AppState now builds the embedder
with ``use_local_fallback=False`` so API errors fail loud instead.
"""

from __future__ import annotations

from perspicacite.llm.embeddings import (
    FallbackEmbeddingProvider,
    LiteLLMEmbeddingProvider,
    create_embedding_provider,
)


def test_api_model_without_fallback_is_pure_litellm():
    # what AppState builds now: no cross-dimension fallback.
    p = create_embedding_provider("text-embedding-3-large", use_local_fallback=False)
    assert isinstance(p, LiteLLMEmbeddingProvider)
    assert not isinstance(p, FallbackEmbeddingProvider)


def test_api_model_with_fallback_would_wrap_local():
    # the old (poisoning) behaviour, kept as a guard so the rationale is explicit.
    p = create_embedding_provider("text-embedding-3-large", use_local_fallback=True)
    assert isinstance(p, FallbackEmbeddingProvider)


def test_local_model_is_unaffected_by_flag():
    # local models never get a fallback wrapper either way (they need no network).
    from perspicacite.llm.embeddings import SentenceTransformerEmbeddingProvider

    for flag in (True, False):
        p = create_embedding_provider("all-MiniLM-L6-v2", use_local_fallback=flag)
        assert isinstance(p, SentenceTransformerEmbeddingProvider)
