# tests/unit/test_llm_cache_storage.py
"""Tests for LLMResponseCache SQLite storage layer (Wave 2.1)."""
import asyncio
import time
from pathlib import Path

import pytest

from perspicacite.llm.cache import LLMResponseCache, build_cache_key


def _key() -> str:
    return build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, max_tokens=100, extra_kwargs={},
    )


@pytest.fixture
def cache(tmp_path: Path) -> LLMResponseCache:
    return LLMResponseCache(path=tmp_path / "cache.db", ttl_hours=24)


@pytest.mark.asyncio
async def test_get_returns_none_on_miss(cache):
    assert await cache.get(_key()) is None


@pytest.mark.asyncio
async def test_put_then_get_roundtrip(cache):
    k = _key()
    await cache.put(
        key=k, provider="anthropic", model="claude-haiku-4-5",
        response="hello world", latency_ms=123.4,
        input_tokens=5, output_tokens=2,
    )
    hit = await cache.get(k)
    assert hit is not None
    assert hit.response == "hello world"
    assert hit.provider == "anthropic"
    assert hit.model == "claude-haiku-4-5"
    assert hit.latency_ms == pytest.approx(123.4)
    assert hit.input_tokens == 5
    assert hit.output_tokens == 2


@pytest.mark.asyncio
async def test_get_returns_none_after_ttl_expiry(tmp_path):
    cache = LLMResponseCache(path=tmp_path / "ttl.db", ttl_hours=1)
    k = _key()
    # Insert a row dated 2 hours ago.
    await cache.put(
        key=k, provider="anthropic", model="m",
        response="stale", latency_ms=0.0,
        input_tokens=0, output_tokens=0,
        _created_at_override=int(time.time()) - 2 * 3600,
    )
    assert await cache.get(k) is None


@pytest.mark.asyncio
async def test_ttl_zero_means_forever(tmp_path):
    cache = LLMResponseCache(path=tmp_path / "forever.db", ttl_hours=0)
    k = _key()
    await cache.put(
        key=k, provider="anthropic", model="m",
        response="ancient", latency_ms=0.0,
        input_tokens=0, output_tokens=0,
        _created_at_override=int(time.time()) - 1_000_000,  # ~11 days
    )
    hit = await cache.get(k)
    assert hit is not None
    assert hit.response == "ancient"


@pytest.mark.asyncio
async def test_purge_expired_deletes_old_rows(tmp_path):
    cache = LLMResponseCache(path=tmp_path / "purge.db", ttl_hours=1)
    now = int(time.time())
    await cache.put(
        key="old", provider="p", model="m",
        response="old", latency_ms=0.0, input_tokens=0, output_tokens=0,
        _created_at_override=now - 2 * 3600,
    )
    await cache.put(
        key="new", provider="p", model="m",
        response="new", latency_ms=0.0, input_tokens=0, output_tokens=0,
        _created_at_override=now,
    )
    n = await cache.purge_expired()
    assert n == 1
    assert await cache.get("new") is not None


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_path):
    """WAL gives us concurrent-reader safety. Verify the pragma stuck."""
    cache = LLMResponseCache(path=tmp_path / "wal.db", ttl_hours=24)
    # Touch the DB so it actually exists.
    await cache.get("nope")
    import sqlite3
    with sqlite3.connect(tmp_path / "wal.db") as conn:
        mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    assert mode.lower() == "wal"


@pytest.mark.asyncio
async def test_concurrent_puts_dont_corrupt(tmp_path):
    """20 concurrent writes — all rows must land, no exceptions."""
    cache = LLMResponseCache(path=tmp_path / "concurrent.db", ttl_hours=24)

    async def write(i: int):
        await cache.put(
            key=f"k{i}", provider="p", model="m",
            response=f"r{i}", latency_ms=0.0,
            input_tokens=0, output_tokens=0,
        )

    await asyncio.gather(*(write(i) for i in range(20)))
    for i in range(20):
        hit = await cache.get(f"k{i}")
        assert hit is not None
        assert hit.response == f"r{i}"


@pytest.mark.asyncio
async def test_overwrite_replaces_existing(cache):
    """Re-putting the same key with a fresh response replaces, not appends."""
    k = _key()
    await cache.put(
        key=k, provider="p", model="m", response="v1",
        latency_ms=0.0, input_tokens=0, output_tokens=0,
    )
    await cache.put(
        key=k, provider="p", model="m", response="v2",
        latency_ms=0.0, input_tokens=0, output_tokens=0,
    )
    hit = await cache.get(k)
    assert hit is not None and hit.response == "v2"
