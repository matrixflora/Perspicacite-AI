# Embedding cache — design spec

**Wave 2.2 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Cache embedding vectors keyed by `(model, text-hash)` so that
re-building or extending a knowledge base does not re-embed chunks the
provider already saw. This is the dominant cost on every multi-paper
ingest and every contextual-retrieval re-run.

## Non-goals

- Caching `query`-time embeddings of user questions — these are
  short, low-volume, low-value to cache. Out of scope for v1.
- Compressing the BLOB (zstd / quantization) — float32 × 384 dim is
  ~1.5 KB per vector; 100k chunks ≈ 150 MB. Acceptable footprint for
  a single-user research tool. Revisit if disk grows past 1 GB.
- Replacing Chroma's own embedding storage — Chroma owns the
  retrieval-time vectors. This cache is upstream of Chroma; it stops
  us from *recomputing* the vectors before they are written into
  Chroma.

## Architecture

A `CachedEmbeddingProvider` class wraps an existing
`EmbeddingProvider` and consults a SQLite cache before forwarding a
batch to the inner provider. The `create_embedding_provider` factory
is taught to wrap the result when caching is enabled in config.

The cache is **per-text**, not per-batch. Two overlapping batches
share entries. This is essential — re-ingesting a paper with one new
chunk should only embed that one chunk, not the whole paper.

```
provider.embed([t1, t2, t3, t4])
  └── cache.get_many([h(t1), h(t2), h(t3), h(t4)])
        ├── hits  = {h(t1): vec_t1, h(t3): vec_t3}
        └── miss  = [t2, t4]
                inner.embed(miss)
                cache.put_many([(h(t2), vec_t2), (h(t4), vec_t4)])
                merge in original order → [vec_t1, vec_t2, vec_t3, vec_t4]
```

### Cache key

SHA256 of `model_name || "\x00" || text`. The null byte separator
makes ambiguity impossible across model boundaries (no
`"foobar" + ""` vs `"foo" + "bar"` ambiguity).

The model name is part of the key — switching from `all-MiniLM-L6-v2`
to `all-mpnet-base-v2` correctly invalidates the cache without
explicit user action.

### Schema

```sql
CREATE TABLE embedding_cache (
  key          TEXT PRIMARY KEY,           -- sha256(model || \0 || text)
  model        TEXT NOT NULL,
  dimension    INTEGER NOT NULL,
  embedding    BLOB NOT NULL,              -- float32 little-endian numpy bytes
  created_at   INTEGER NOT NULL
);
CREATE INDEX idx_embedding_cache_created_at ON embedding_cache (created_at);
CREATE INDEX idx_embedding_cache_model      ON embedding_cache (model);
```

`embedding` is stored as `np.asarray(v, dtype=np.float32).tobytes()`.
On read: `np.frombuffer(blob, dtype=np.float32).tolist()`. Roughly
2× smaller than JSON; faster to read.

### TTL

- `cache_ttl_days: 0` default = **forever**. Embeddings are
  deterministic per `(model, text)`. They don't drift like LLM
  responses do.
- `> 0` enables expiry (rare; for users who want to bound disk).
- Lazy GC on read, same pattern as Wave 2.1.

### Bypass / invalidation

- Per-call: `provider.embed(texts, cache=False)` skips both paths.
  Used by tests that need to exercise the inner provider directly.
- Config: `kb.embedding_cache_enabled: false` disables globally.
- Manual clear: `rm data/embedding_cache.db*`. No CLI in v1.

### Concurrency

SQLite WAL mode, short-lived connections per call, identical pattern
to the LLM cache. Embedding pipelines run dozens of batches in
parallel during a multi-paper ingest — WAL handles this fine.

### Empty / whitespace texts

The existing providers replace empty/whitespace-only texts with a
zero vector of `self.dimension`. The cache must preserve this
behaviour — we never write a zero-vector cache entry for an empty
input; we just return the zero vector inline. This keeps the cache
clean (no garbage entries) and respects the existing contract.

## Components

| File | Responsibility |
|---|---|
| `src/perspicacite/llm/embedding_cache.py` (new) | `EmbeddingCache` class (SQLite layer) + `build_embedding_cache_key`. |
| `src/perspicacite/llm/embeddings.py` (modify) | New `CachedEmbeddingProvider` wrapper. `create_embedding_provider` factory wraps when `cache_enabled`. |
| `src/perspicacite/config/schema.py` (modify) | Add `embedding_cache_enabled`, `embedding_cache_path`, `embedding_cache_ttl_days` to `KnowledgeBaseConfig`. |
| `tests/unit/test_embedding_cache_key.py` (new) | Key stability tests (~6 tests). |
| `tests/unit/test_embedding_cache_storage.py` (new) | SQLite roundtrip tests (~8 tests). |
| `tests/unit/test_cached_embedding_provider.py` (new) | Wrapper-level tests with a mock inner provider (~6 tests). |

## Config example

```yaml
kb:
  embedding_model: text-embedding-3-small
  embedding_cache_enabled: true
  embedding_cache_path: data/embedding_cache.db
  embedding_cache_ttl_days: 0           # 0 = forever
```

## Test plan

- **Unit (`test_embedding_cache_key.py`):**
  - Stable across calls.
  - Differs on model.
  - Differs on text.
  - Stable across model+text concatenation ambiguity (null-byte
    separator works).
  - Empty text raises ValueError (the wrapper handles empty texts
    upstream — the cache layer should never see them).
  - Output is 64-char hex.

- **Unit (`test_embedding_cache_storage.py`):**
  - Get-miss returns None.
  - Put + get roundtrip preserves the vector to float-32 precision.
  - Batch `get_many` returns a `{key: vector}` dict for hits and
    omits misses.
  - Batch `put_many` inserts N rows atomically.
  - TTL expiry (with `_created_at_override`).
  - `ttl_days=0` keeps ancient rows.
  - WAL mode enabled.
  - Concurrent `put_many` calls don't corrupt.

- **Unit (`test_cached_embedding_provider.py`):**
  - First call: all texts go to the inner provider, all are cached.
  - Second call (same texts): inner provider not invoked, all cached.
  - Partial overlap: only the new texts hit the inner provider.
  - Empty / whitespace texts pass through to the existing zero-vector
    path without touching the cache.
  - `cache=False` bypasses both ways.
  - Order is preserved: the returned list matches the input order
    even when the inner provider only saw a subset.

## Rollout

- **Default-on.** Embeddings are deterministic; staleness risk is
  zero. The disk-footprint risk is bounded by typical KB sizes
  (~150 MB for 100k chunks).
- No migration; `CREATE TABLE IF NOT EXISTS`.

## Followups (not in scope for v2.2)

- Compression (zstd) — only if footprint becomes a problem.
- LRU bound — only if footprint becomes a problem.
- Cache query-time embeddings — low value, separate decision.
- Cache hit rate metric — log lines suffice for now.
