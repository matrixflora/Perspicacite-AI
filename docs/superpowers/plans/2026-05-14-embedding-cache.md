# Embedding cache — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cache embedding vectors keyed by `(model, text)` so re-running
ingests doesn't re-embed the same chunks.

**Architecture:** New `EmbeddingCache` (SQLite, BLOB storage) +
`CachedEmbeddingProvider` wrapper composed automatically by the existing
`create_embedding_provider` factory when caching is enabled.

**Tech stack:** stdlib `sqlite3`, `numpy` (already a dep), `hashlib`.

**Spec:** `docs/superpowers/specs/2026-05-14-embedding-cache-design.md`

---

## Task 1: Config schema additions

**Files:**
- Modify: `src/perspicacite/config/schema.py` (the `KnowledgeBaseConfig` class around line 47)
- Test: `tests/unit/test_config_embedding_cache_fields.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_config_embedding_cache_fields.py -v
```

Expected: 4 errors on `AttributeError: ... 'embedding_cache_enabled'`.

- [ ] **Step 3: Add the fields**

In `src/perspicacite/config/schema.py`, inside `KnowledgeBaseConfig`,
after the last existing field (`contextual_retrieval_max_chars`,
around line 108) and before the next class, add:

```python
    # ---- embedding cache (Wave 2.2) --------------------------------
    # Cache embedding vectors keyed by (model, text). Embeddings are
    # deterministic per (model, text), so the cache is safe to keep
    # forever by default. See
    # docs/superpowers/specs/2026-05-14-embedding-cache-design.md.
    embedding_cache_enabled: bool = Field(
        default=True,
        description=(
            "Cache embedding vectors on disk so repeated ingests don't "
            "re-embed identical chunks. Default on; per-call bypass via "
            "provider.embed(..., cache=False)."
        ),
    )
    embedding_cache_path: Path = Field(
        default=Path("data/embedding_cache.db"),
        description=(
            "SQLite file backing the embedding cache. Covered by the "
            "data/*.db .gitignore rule."
        ),
    )
    embedding_cache_ttl_days: int = Field(
        default=0,
        ge=0,
        description=(
            "Days before a cached embedding expires. 0 = forever "
            "(default — embeddings are deterministic per model+text)."
        ),
    )
```

- [ ] **Step 4: Run, watch pass; also re-run config audit**

```bash
pytest tests/unit/test_config_embedding_cache_fields.py -v
pytest tests/integration/test_config_audit.py -v
```

Expected: 4 PASSED and 12 PASSED respectively.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_config_embedding_cache_fields.py
git commit -m "feat(config): embedding_cache_{enabled,path,ttl_days} on KnowledgeBaseConfig (Wave 2.2)"
```

---

## Task 2: Cache-key builder

**Files:**
- Create: `src/perspicacite/llm/embedding_cache.py`
- Test: `tests/unit/test_embedding_cache_key.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_embedding_cache_key.py
"""Cache-key tests for the embedding cache (Wave 2.2)."""
import pytest

from perspicacite.llm.embedding_cache import build_embedding_cache_key


def test_key_stable():
    a = build_embedding_cache_key(model="m1", text="hello world")
    b = build_embedding_cache_key(model="m1", text="hello world")
    assert a == b
    assert len(a) == 64


def test_key_differs_on_model():
    a = build_embedding_cache_key(model="m1", text="hello")
    b = build_embedding_cache_key(model="m2", text="hello")
    assert a != b


def test_key_differs_on_text():
    a = build_embedding_cache_key(model="m1", text="hello")
    b = build_embedding_cache_key(model="m1", text="world")
    assert a != b


def test_key_disambiguates_concatenation():
    """The null-byte separator prevents collisions between
    (model='ab', text='c') and (model='a', text='bc')."""
    a = build_embedding_cache_key(model="ab", text="c")
    b = build_embedding_cache_key(model="a", text="bc")
    assert a != b


def test_key_rejects_empty_text():
    """Empty texts should never reach the cache — the wrapper handles
    them with the zero-vector contract before we get here."""
    with pytest.raises(ValueError):
        build_embedding_cache_key(model="m", text="")


def test_key_rejects_empty_model():
    with pytest.raises(ValueError):
        build_embedding_cache_key(model="", text="text")
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_embedding_cache_key.py -v
```

- [ ] **Step 3: Implement**

Create `src/perspicacite/llm/embedding_cache.py`:

```python
"""On-disk cache for embedding vectors.

See docs/superpowers/specs/2026-05-14-embedding-cache-design.md.
The cache is per-text (not per-batch), so overlapping batches share
entries. Vectors are stored as float32 BLOBs.
"""

from __future__ import annotations

import hashlib


def build_embedding_cache_key(*, model: str, text: str) -> str:
    """Compute the SHA256 cache key for an (model, text) pair.

    The null-byte separator prevents ambiguity at the model/text
    boundary (no ``"foobar" + ""`` vs ``"foo" + "bar"`` collisions).
    Empty inputs raise ``ValueError`` — the wrapper handles those
    upstream with the zero-vector contract.
    """
    if not model:
        raise ValueError("model must be non-empty")
    if not text:
        raise ValueError("text must be non-empty")
    payload = model.encode("utf-8") + b"\x00" + text.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_embedding_cache_key.py -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/embedding_cache.py tests/unit/test_embedding_cache_key.py
git commit -m "feat(embedding-cache): SHA256 key with null-byte separator (Wave 2.2)"
```

---

## Task 3: EmbeddingCache SQLite layer

**Files:**
- Modify: `src/perspicacite/llm/embedding_cache.py` (append)
- Test: `tests/unit/test_embedding_cache_storage.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_embedding_cache_storage.py -v
```

- [ ] **Step 3: Implement EmbeddingCache**

Append to `src/perspicacite/llm/embedding_cache.py`:

```python
import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np


_SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache (
    key          TEXT PRIMARY KEY,
    model        TEXT NOT NULL,
    dimension    INTEGER NOT NULL,
    embedding    BLOB NOT NULL,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embedding_cache_created_at
    ON embedding_cache (created_at);
CREATE INDEX IF NOT EXISTS idx_embedding_cache_model
    ON embedding_cache (model);
"""


def _encode(vec: Sequence[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def _decode(blob: bytes) -> list[float]:
    return np.frombuffer(blob, dtype=np.float32).tolist()


class EmbeddingCache:
    """SQLite-backed cache for embedding vectors.

    Vectors are stored as float32 BLOBs (~1.5 KB per 384-dim vector).
    Keys come from :func:`build_embedding_cache_key`. TTL defaults to
    forever — embeddings are deterministic per ``(model, text)`` and
    don't drift.
    """

    def __init__(self, path: Path | str, ttl_days: int = 0) -> None:
        self.path = Path(path)
        self.ttl_days = int(ttl_days)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)

    def _ttl_cutoff(self) -> int:
        if self.ttl_days <= 0:
            return 0
        return int(time.time()) - self.ttl_days * 86400

    # ---- get -----------------------------------------------------------

    async def get(self, key: str) -> list[float] | None:
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> list[float] | None:
        cutoff = self._ttl_cutoff()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT embedding, created_at FROM embedding_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            blob, created_at = row
            if created_at < cutoff:
                conn.execute("DELETE FROM embedding_cache WHERE key = ?", (key,))
                conn.commit()
                return None
        return _decode(blob)

    async def get_many(self, keys: Sequence[str]) -> dict[str, list[float]]:
        return await asyncio.to_thread(self._get_many_sync, list(keys))

    def _get_many_sync(self, keys: list[str]) -> dict[str, list[float]]:
        if not keys:
            return {}
        cutoff = self._ttl_cutoff()
        # SQLite parameter cardinality cap ≈ 999 — chunk if needed.
        out: dict[str, list[float]] = {}
        expired: list[str] = []
        with self._connect() as conn:
            for i in range(0, len(keys), 500):
                chunk = keys[i : i + 500]
                placeholders = ",".join("?" * len(chunk))
                rows = conn.execute(
                    "SELECT key, embedding, created_at FROM embedding_cache "
                    f"WHERE key IN ({placeholders})",
                    chunk,
                ).fetchall()
                for key, blob, created_at in rows:
                    if created_at < cutoff:
                        expired.append(key)
                        continue
                    out[key] = _decode(blob)
            if expired:
                placeholders = ",".join("?" * len(expired))
                conn.execute(
                    f"DELETE FROM embedding_cache WHERE key IN ({placeholders})",
                    expired,
                )
                conn.commit()
        return out

    # ---- put -----------------------------------------------------------

    async def put(
        self,
        *,
        key: str,
        model: str,
        embedding: Sequence[float],
        _created_at_override: int | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._put_sync, key, model, embedding, _created_at_override,
        )

    def _put_sync(
        self,
        key: str,
        model: str,
        embedding: Sequence[float],
        created_at_override: int | None,
    ) -> None:
        blob = _encode(embedding)
        created_at = (
            int(created_at_override)
            if created_at_override is not None
            else int(time.time())
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO embedding_cache "
                "(key, model, dimension, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, model, len(embedding), blob, created_at),
            )
            conn.commit()

    async def put_many(
        self, items: Iterable[tuple[str, str, Sequence[float]]],
    ) -> None:
        await asyncio.to_thread(self._put_many_sync, list(items))

    def _put_many_sync(
        self,
        items: list[tuple[str, str, Sequence[float]]],
    ) -> None:
        if not items:
            return
        now = int(time.time())
        rows = [
            (key, model, len(vec), _encode(vec), now)
            for key, model, vec in items
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO embedding_cache "
                "(key, model, dimension, embedding, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            conn.commit()
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_embedding_cache_storage.py -v
```

Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/embedding_cache.py tests/unit/test_embedding_cache_storage.py
git commit -m "feat(embedding-cache): SQLite storage layer with batch ops + WAL (Wave 2.2)"
```

---

## Task 4: CachedEmbeddingProvider wrapper

**Files:**
- Modify: `src/perspicacite/llm/embeddings.py`
- Test: `tests/unit/test_cached_embedding_provider.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run, watch fail**

```bash
pytest tests/unit/test_cached_embedding_provider.py -v
```

- [ ] **Step 3: Implement the wrapper**

Append to `src/perspicacite/llm/embeddings.py`:

```python
class CachedEmbeddingProvider:
    """Wraps an :class:`EmbeddingProvider`, consulting an on-disk cache
    before forwarding uncached texts to the inner provider.

    Per-text keying: two overlapping batches share entries. Empty /
    whitespace inputs pass through to the zero-vector contract without
    touching the cache. See
    docs/superpowers/specs/2026-05-14-embedding-cache-design.md.
    """

    def __init__(self, *, inner: Any, cache: Any) -> None:
        self.inner = inner
        self.cache = cache

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    @property
    def dimension(self) -> int:
        return self.inner.dimension

    async def embed(
        self,
        texts: list[str],
        cache: bool = True,
    ) -> list[list[float]]:
        if not texts:
            return []

        # Build per-text keys, but only for non-empty texts (matches
        # the inner providers' empty-input contract).
        from perspicacite.llm.embedding_cache import build_embedding_cache_key

        zero = [0.0] * self.inner.dimension
        keys: list[str | None] = []
        for t in texts:
            if not t or not t.strip():
                keys.append(None)
            else:
                keys.append(
                    build_embedding_cache_key(model=self.inner.model_name, text=t)
                )

        # Cache-bypass: straight to inner, no read, no write.
        if not cache:
            # Inner provider already handles empties → zero vec.
            return await self.inner.embed(texts)

        # Batch read.
        non_null_keys = [k for k in keys if k is not None]
        hits = await self.cache.get_many(non_null_keys) if non_null_keys else {}

        # Build the result list, collecting misses to send to inner.
        out: list[list[float] | None] = [None] * len(texts)
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        for i, (t, k) in enumerate(zip(texts, keys)):
            if k is None:
                out[i] = zero  # empty/whitespace stays zero-vector
            elif k in hits:
                out[i] = hits[k]
            else:
                miss_indices.append(i)
                miss_texts.append(t)

        if miss_texts:
            new_vecs = await self.inner.embed(miss_texts)
            # Write to cache + slot into out in original order.
            put_items: list[tuple[str, str, list[float]]] = []
            for idx, vec in zip(miss_indices, new_vecs):
                out[idx] = vec
                k = keys[idx]
                if k is not None:
                    put_items.append((k, self.inner.model_name, vec))
            if put_items:
                await self.cache.put_many(put_items)

        # Final result — every slot is filled.
        return [v if v is not None else zero for v in out]
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_cached_embedding_provider.py -v
```

Expected: 6 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/embeddings.py tests/unit/test_cached_embedding_provider.py
git commit -m "feat(embedding-cache): CachedEmbeddingProvider wrapper (Wave 2.2)"
```

---

## Task 5: Factory integration

**Files:**
- Modify: `src/perspicacite/llm/embeddings.py` (the `create_embedding_provider` factory at the bottom)
- Test: `tests/unit/test_embedding_factory_caching.py` (new)

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Update `create_embedding_provider`**

Replace the existing factory at the bottom of `src/perspicacite/llm/embeddings.py`
with:

```python
def create_embedding_provider(
    model: str,
    use_local_fallback: bool = True,
    *,
    cache_enabled: bool = False,
    cache_path: "Path | str | None" = None,
    cache_ttl_days: int = 0,
) -> EmbeddingProvider:
    """
    Factory function to create an embedding provider.

    Args:
        model: Model name (e.g., 'text-embedding-3-small' or 'all-MiniLM-L6-v2')
        use_local_fallback: Whether to set up local fallback for API providers.
        cache_enabled: When True, wrap the returned provider in a
            :class:`CachedEmbeddingProvider`. The cache key is
            ``sha256(model || \\x00 || text)``, so switching models
            transparently invalidates the cache.
        cache_path: SQLite file backing the cache. Required when
            ``cache_enabled`` is True.
        cache_ttl_days: Days until a cached vector expires. 0 (default) =
            keep forever. Embeddings are deterministic per (model, text),
            so this is safe.

    Returns:
        EmbeddingProvider instance (possibly wrapped in caching).
    """
    # Inner-provider selection (unchanged logic)
    if model.startswith("all-") or "/" not in model and "embedding" not in model:
        inner: EmbeddingProvider = SentenceTransformerEmbeddingProvider(model=model)
    else:
        primary = LiteLLMEmbeddingProvider(model=model)
        if use_local_fallback:
            fallback = SentenceTransformerEmbeddingProvider()
            inner = FallbackEmbeddingProvider(primary, fallback)
        else:
            inner = primary

    if not cache_enabled:
        return inner

    if cache_path is None:
        raise ValueError(
            "create_embedding_provider(cache_enabled=True) requires cache_path"
        )

    # Import lazily to avoid importing numpy unless the cache is used.
    from perspicacite.llm.embedding_cache import EmbeddingCache

    cache = EmbeddingCache(path=cache_path, ttl_days=cache_ttl_days)
    return CachedEmbeddingProvider(inner=inner, cache=cache)
```

You'll also need to add the missing `from pathlib import Path` import
(check if present; add at the top of the file).

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_embedding_factory_caching.py -v
```

Expected: 3 PASSED.

Also run a broader sanity check on embeddings (skipping the heavy
SentenceTransformer instantiation tests is fine — they still skip via
the standard ignore list):

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -5
```

Expected: pass count grew by the new tests; no NEW failures vs the
Wave 1.1 baseline (12 pre-existing).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/embeddings.py tests/unit/test_embedding_factory_caching.py
git commit -m "feat(embedding-cache): factory wires cache opt-in (Wave 2.2)"
```

---

## Task 6: Status doc

**Files:**
- Create: `docs/embedding-cache-2026-05-14.md`
- Modify: `.gitignore` (add allowlist)

- [ ] **Step 1: Write the doc**

```markdown
# Embedding cache — status & operator guide (2026-05-14)

Wave 2.2 of the framework-hardening roadmap. Disk-cached embedding
vectors for every `EmbeddingProvider`.

## What it does

`CachedEmbeddingProvider` wraps an inner provider (SentenceTransformer
or LiteLLM) and consults `data/embedding_cache.db` per text. On a
hit, the vector returns in <5 ms instead of re-running the encoder /
re-calling the API. Per-text keying means two overlapping batches
share entries — re-ingesting a paper with one new chunk only embeds
that one chunk.

## Why it pays off

- Multi-paper ingest: the dominant cost is the encoder. Identical
  chunks (introduction boilerplate, citation lines, etc.) across
  papers re-hit cache.
- Contextual retrieval iteration: re-running with a different
  retrieval tier reuses every embedding.
- Switching from LiteLLM to local fallback (or vice-versa) without
  re-embedding the bulk: the cache key embeds the model name, so
  switching invalidates correctly but only for the swapped paths.

## Configuration

```yaml
kb:
  embedding_model: text-embedding-3-small
  embedding_cache_enabled: true                 # default true
  embedding_cache_path: data/embedding_cache.db # default
  embedding_cache_ttl_days: 0                   # default forever
```

## Bypass

```python
await provider.embed(texts, cache=False)
```

Used by tests that want to exercise the inner provider directly.

## Clearing

```bash
rm data/embedding_cache.db data/embedding_cache.db-shm data/embedding_cache.db-wal
```

Selective by model:

```bash
sqlite3 data/embedding_cache.db \
  "DELETE FROM embedding_cache WHERE model = 'text-embedding-3-small';"
```

## Storage footprint

float32 × dim ≈ 4·dim bytes per vector.

| Model | Dim | Per vector | 100k chunks |
|---|---|---|---|
| all-MiniLM-L6-v2 | 384 | 1.5 KB | 150 MB |
| all-mpnet-base-v2 | 768 | 3.0 KB | 300 MB |
| text-embedding-3-small | 1536 | 6.0 KB | 600 MB |
| text-embedding-3-large | 3072 | 12.0 KB | 1.2 GB |

Acceptable up to ~1 GB. Beyond that, revisit compression (zstd) or
LRU bounds.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/embedding_cache.py` | `EmbeddingCache` + key builder |
| `src/perspicacite/llm/embeddings.py` | `CachedEmbeddingProvider` + factory wiring |
| `src/perspicacite/config/schema.py` | Three new fields on `KnowledgeBaseConfig` |
| `tests/unit/test_embedding_cache_*.py` | Storage, key, wrapper tests |

## Open followups

- Wire `create_embedding_provider` callsites (mcp/server.py, rag/engine.py,
  retrieval/chroma_store.py) to pass cache config from `LLMConfig`.
  This plan ships the building blocks; callsite migration is a
  follow-up commit once we audit each entry point.
- Compression (zstd) — only if footprint > 1 GB.
- LRU bound — only if footprint > 1 GB.
- Query-time embedding caching — low value, separate decision.
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/embedding-cache-*.md` to `.gitignore` (after the existing
`!docs/llm-cache-*.md` line if present, otherwise next to the other
docs allowlist lines).

- [ ] **Step 3: Commit**

```bash
git add docs/embedding-cache-2026-05-14.md .gitignore
git commit -m "docs(embedding-cache): operator guide (Wave 2.2)"
```

---

## Done

After Task 6:

- New module `src/perspicacite/llm/embedding_cache.py` (~200 LoC).
- One new class in `embeddings.py` (`CachedEmbeddingProvider`).
- Three new fields on `KnowledgeBaseConfig`.
- Factory wires caching when `cache_enabled` + `cache_path` supplied.
- 26 new tests, all passing.
- Operator doc landed.
- Wave 1.1 baseline preserved.

Callsite migration (passing cache config to `create_embedding_provider`
from the orchestrator) is a deliberate follow-up — small, mechanical,
done in a separate commit once we audit the four entry points.
