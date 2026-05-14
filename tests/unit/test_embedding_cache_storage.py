# tests/unit/test_embedding_cache_storage.py
"""SQLite-layer tests for EmbeddingCache (Wave 2.2)."""
import asyncio
import time
from pathlib import Path

import numpy as np
import pytest

from perspicacite.llm.embedding_cache import EmbeddingCache


@pytest.fixture
def cache(tmp_path: Path) -> EmbeddingCache:
    return EmbeddingCache(path=tmp_path / "embed.db", ttl_days=0)


@pytest.mark.asyncio
async def test_get_miss_returns_none(cache):
    assert await cache.get("nope") is None


@pytest.mark.asyncio
async def test_put_then_get_preserves_vector(cache):
    vec = [0.1, 0.2, 0.3, 0.4]
    await cache.put(key="k1", model="m", embedding=vec)
    out = await cache.get("k1")
    assert out is not None
    # float32 precision tolerance
    assert np.allclose(out, vec, atol=1e-6)


@pytest.mark.asyncio
async def test_get_many_returns_only_hits(cache):
    await cache.put(key="a", model="m", embedding=[1.0, 2.0])
    await cache.put(key="b", model="m", embedding=[3.0, 4.0])
    hits = await cache.get_many(["a", "b", "c"])
    assert set(hits.keys()) == {"a", "b"}
    assert np.allclose(hits["a"], [1.0, 2.0], atol=1e-6)
    assert np.allclose(hits["b"], [3.0, 4.0], atol=1e-6)


@pytest.mark.asyncio
async def test_put_many_inserts_batch(cache):
    items = [
        ("k1", "m", [0.1, 0.2]),
        ("k2", "m", [0.3, 0.4]),
        ("k3", "m", [0.5, 0.6]),
    ]
    await cache.put_many(items)
    hits = await cache.get_many(["k1", "k2", "k3"])
    assert len(hits) == 3


@pytest.mark.asyncio
async def test_ttl_expiry(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "ttl.db", ttl_days=1)
    await cache.put(
        key="old", model="m", embedding=[1.0],
        _created_at_override=int(time.time()) - 2 * 86400,
    )
    assert await cache.get("old") is None


@pytest.mark.asyncio
async def test_ttl_zero_keeps_ancient(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "forever.db", ttl_days=0)
    await cache.put(
        key="ancient", model="m", embedding=[1.0],
        _created_at_override=int(time.time()) - 10_000_000,
    )
    out = await cache.get("ancient")
    assert out is not None


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "wal.db", ttl_days=0)
    await cache.get("nope")
    import sqlite3
    with sqlite3.connect(tmp_path / "wal.db") as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_concurrent_put_many_safe(tmp_path):
    cache = EmbeddingCache(path=tmp_path / "c.db", ttl_days=0)

    async def worker(n: int):
        await cache.put_many([
            (f"k{n}_{i}", "m", [float(i)]) for i in range(10)
        ])

    await asyncio.gather(*(worker(n) for n in range(5)))
    # 50 keys total
    keys = [f"k{n}_{i}" for n in range(5) for i in range(10)]
    hits = await cache.get_many(keys)
    assert len(hits) == 50
