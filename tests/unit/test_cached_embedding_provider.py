# tests/unit/test_cached_embedding_provider.py
"""Wrapper-level tests for CachedEmbeddingProvider (Wave 2.2)."""
from pathlib import Path

import numpy as np
import pytest

from perspicacite.llm.embedding_cache import EmbeddingCache
from perspicacite.llm.embeddings import CachedEmbeddingProvider


class _FakeInner:
    """Deterministic 3-dim provider: vec = [len(text), ord(text[0]), 0.5]."""
    model_name = "fake-model"
    dimension = 3

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        out: list[list[float]] = []
        for t in texts:
            if not t or not t.strip():
                out.append([0.0, 0.0, 0.0])
            else:
                out.append([float(len(t)), float(ord(t[0])), 0.5])
        return out


@pytest.fixture
def cached(tmp_path: Path):
    cache = EmbeddingCache(path=tmp_path / "e.db", ttl_days=0)
    inner = _FakeInner()
    return CachedEmbeddingProvider(inner=inner, cache=cache), inner


@pytest.mark.asyncio
async def test_first_call_populates_cache(cached):
    wrapper, inner = cached
    r1 = await wrapper.embed(["a", "bb", "ccc"])
    assert len(r1) == 3
    assert inner.calls == [["a", "bb", "ccc"]]


@pytest.mark.asyncio
async def test_second_call_returns_cached(cached):
    wrapper, inner = cached
    await wrapper.embed(["a", "bb"])
    inner.calls.clear()
    r2 = await wrapper.embed(["a", "bb"])
    assert inner.calls == []  # no inner call
    assert np.allclose(r2[0], [1.0, float(ord("a")), 0.5], atol=1e-6)


@pytest.mark.asyncio
async def test_partial_overlap_only_uncached_go_to_inner(cached):
    wrapper, inner = cached
    await wrapper.embed(["a", "bb"])
    inner.calls.clear()
    await wrapper.embed(["a", "bb", "ccc"])
    # Only "ccc" should have hit the inner provider.
    assert inner.calls == [["ccc"]]


@pytest.mark.asyncio
async def test_empty_texts_dont_touch_cache(cached):
    """Whitespace/empty stays in the zero-vector path — never cached."""
    wrapper, inner = cached
    r = await wrapper.embed(["", "  ", "real"])
    # Only "real" hits the inner.
    assert inner.calls == [["real"]]
    # All three returned in order, with zero vectors for empties.
    assert r[0] == [0.0, 0.0, 0.0]
    assert r[1] == [0.0, 0.0, 0.0]
    assert np.allclose(r[2], [4.0, float(ord("r")), 0.5], atol=1e-6)


@pytest.mark.asyncio
async def test_cache_false_bypasses(cached):
    wrapper, inner = cached
    await wrapper.embed(["a"])
    inner.calls.clear()
    await wrapper.embed(["a"], cache=False)
    assert inner.calls == [["a"]]


@pytest.mark.asyncio
async def test_order_preserved(cached):
    """Result list order must match input order even when some entries
    came from cache and others from the inner provider."""
    wrapper, inner = cached
    await wrapper.embed(["x", "z"])  # prime
    inner.calls.clear()
    r = await wrapper.embed(["x", "newtext", "z"])
    # "newtext" was the only miss
    assert inner.calls == [["newtext"]]
    assert np.allclose(r[0], [1.0, float(ord("x")), 0.5], atol=1e-6)
    assert np.allclose(r[1], [7.0, float(ord("n")), 0.5], atol=1e-6)
    assert np.allclose(r[2], [1.0, float(ord("z")), 0.5], atol=1e-6)
