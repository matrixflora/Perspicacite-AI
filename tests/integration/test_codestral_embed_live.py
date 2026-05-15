"""Live smoke test for Mistral's codestral-embed via LiteLLM.

Why: 2026-05-15 audit P3 follow-up flagged that mistral/codestral-embed
had only stub-level test coverage. This is a live smoke test that
embeds a real code snippet and checks the response shape.

Requires the MISTRAL_API_KEY env var. Skipped otherwise.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("MISTRAL_API_KEY"),
    reason="MISTRAL_API_KEY not set — skip live codestral-embed test",
)


@pytest.mark.asyncio
async def test_codestral_embed_returns_vector_for_code():
    """Embed a small Python snippet via mistral/codestral-embed.

    Asserts:
      - the response is a list of vectors, one per input
      - each vector is a non-empty list of floats
      - the vector is not all zeros (i.e. the fallback zero-vector
        branch in LiteLLMEmbeddingProvider didn't trigger)
    """
    from perspicacite.llm.embeddings import LiteLLMEmbeddingProvider

    provider = LiteLLMEmbeddingProvider(model="mistral/codestral-embed")

    code_snippet = (
        "def fibonacci(n: int) -> int:\n"
        "    if n < 2:\n"
        "        return n\n"
        "    return fibonacci(n - 1) + fibonacci(n - 2)\n"
    )

    vectors = await provider.embed([code_snippet])

    assert isinstance(vectors, list)
    assert len(vectors) == 1
    vec = vectors[0]
    assert isinstance(vec, list)
    assert len(vec) > 0, "embedding vector is empty"
    assert all(isinstance(x, float) for x in vec), "vector must be list[float]"
    # Guard against the empty-text fallback ([[0.0] * dim])
    nonzero = sum(1 for x in vec if x != 0.0)
    assert nonzero > 0, "codestral-embed returned an all-zero vector"


@pytest.mark.asyncio
async def test_codestral_embed_batches_two_snippets():
    """Confirm the batch path returns one vector per input."""
    from perspicacite.llm.embeddings import LiteLLMEmbeddingProvider

    provider = LiteLLMEmbeddingProvider(model="mistral/codestral-embed", batch_size=2)

    inputs = [
        "def add(a, b): return a + b",
        "def mul(a, b): return a * b",
    ]
    vectors = await provider.embed(inputs)
    assert len(vectors) == 2
    assert len(vectors[0]) == len(vectors[1]), (
        "codestral-embed must return same-dim vectors per call"
    )
