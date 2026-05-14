# Disk-cached LLM responses — design spec

**Wave 2.1 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Cache `AsyncLLMClient.complete()` responses on disk so that
repeated calls with identical inputs return instantly from a local
SQLite file instead of hitting the provider again. Pays back on every
dev iteration (re-running tests, re-asking the same query while
iterating prompts) and on every agent-CLI path (6–16 s round-trip
becomes <10 ms).

**Non-goals:**

- Caching `stream()` — rare in this codebase, deterministic-replay
  value is low, complicates the API. Out of scope for v1.
- Caching embeddings — that's Wave 2.2 (separate sub-project), uses a
  different key (chunk text hash, not provider/model/messages).
- Distributed / multi-process coordination — single-user research
  tool. SQLite's row-level locking is sufficient.
- Server-side prompt caching (Anthropic ephemeral cache_control) —
  unchanged; remains a *separate optimisation*. The disk cache hits
  *before* we ever speak HTTP, so the two layers compose.

## Architecture

A single `LLMResponseCache` class owns a SQLite connection at
`data/llm_cache.db` (configurable). It is composed into `AsyncLLMClient`
as `self._cache`. The cache is consulted at the **top** of `complete()`
— after stage resolution but before the MCP-sampling / agent-CLI /
LiteLLM branches — so every routing path benefits.

```
complete()
  └── if cache_enabled and not cache=False:
        ├── hit  → return cached.response  (+ provenance write w/ cached=True)
        └── miss → proceed to existing dispatch path
                   └── on success, cache.put(key, response, usage)
                       return response
```

### Cache key

SHA256 of canonical-JSON-serialised tuple:

```python
(provider, model, messages, temperature, max_tokens, extra_kwargs_filtered)
```

- `messages` is serialised as-is (after coercing to a sorted-key form
  for any dict-shaped content blocks).
- `extra_kwargs_filtered` strips keys that don't affect the response:
  `{"stage", "cache", "timeout"}`.
- The hash is the primary key. No collisions in practice (SHA256).

### Schema

```sql
CREATE TABLE llm_cache (
  key            TEXT PRIMARY KEY,
  provider       TEXT NOT NULL,
  model          TEXT NOT NULL,
  response       TEXT NOT NULL,
  created_at     INTEGER NOT NULL,       -- unix seconds
  latency_ms     REAL,                   -- original call latency
  input_tokens   INTEGER,
  output_tokens  INTEGER
);
CREATE INDEX idx_llm_cache_created_at ON llm_cache (created_at);
```

The index supports periodic TTL sweeps. No other indices needed —
all reads are by primary key.

### TTL

- `cache_ttl_hours: 24` default.
- `0` = no expiry (kept forever).
- Reads check `created_at + ttl_hours*3600 > now()` before returning;
  expired rows are deleted on read (lazy GC).
- An optional `LLMResponseCache.purge_expired()` method runs the
  delete-by-created_at sweep — wired into `AsyncLLMClient.__init__`
  with a debounce flag so it runs at most once per process.

### Bypass / invalidation

- Per-call: `await client.complete(..., cache=False)` skips the read
  *and* the write. Used by integration tests that need genuine
  liveness, and by callers that want fresh randomness on high-temp
  draws.
- Config: `llm.cache_enabled: false` disables both paths globally.
- Manual clear: `python -m perspicacite.llm.cache_admin clear [--older-than HOURS]`
  is a thin Click/argparse wrapper. **Defer to v2** — TTL covers 95 %
  of the need. Mention in followups, don't ship.

### Temperature handling

The cache is **temperature-agnostic** — `temperature` participates in
the key, so `temp=0.0` and `temp=0.7` are different cache entries.
Callers who want fresh randomness on a high-temp draw pass
`cache=False`. Rationale: dev-iteration value of deterministic replay
(re-running the same prompt while debugging) outweighs the cost of an
occasional "I wanted a fresh draw and got a cached one" surprise. The
opt-out is one keyword away.

### Concurrency

SQLite in WAL mode with `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;`.
The cache connection is opened once per `AsyncLLMClient`. Writes are
serialised by the writer lock — fine for our concurrency level
(typical bursts: 10–50 calls in flight across stages). No connection
pool needed.

### Provenance integration

On a cache hit, we still call `provenance.collector.add_llm_call(...)`
with the stored `input_tokens`, `output_tokens`, original
`latency_ms`, **and** a new `cached=True` keyword. The collector adds
a `cached` column to its event payload (additive change, no migration
required because provenance rows are JSON blobs).

Cost-accounting downstream (Wave 2.4 budget caps) will read the
`cached` flag to decide whether the call should count against the
budget. Default: cached calls cost $0 (don't charge twice for the
same answer).

## Components

| File | Responsibility |
|---|---|
| `src/perspicacite/llm/cache.py` (new) | `LLMResponseCache` class — SQLite open, key building, get/put/purge. Self-contained, no LiteLLM dependency. |
| `src/perspicacite/llm/client.py` (modify) | Construct cache in `__init__`, call `get()` at top of `complete()`, call `put()` on success. ~30 LoC of additions. |
| `src/perspicacite/config/schema.py` (modify) | Add `cache_enabled: bool = True`, `cache_path: Path = Path("data/llm_cache.db")`, `cache_ttl_hours: int = 24` to `LLMConfig`. |
| `src/perspicacite/provenance/collector.py` (modify) | Accept optional `cached: bool = False` kwarg in `add_llm_call`, store in payload. |
| `tests/unit/test_llm_cache.py` (new) | Coverage: key stability, TTL expiry, bypass, concurrent put, schema migration. ~10 tests. |
| `tests/unit/test_llm_client_cache_integration.py` (new) | Mock LiteLLM. Verify first call hits provider, second call returns cached. ~4 tests. |
| `tests/integration/test_provider_matrix.py` (modify) | Add `cache=False` to liveness calls so we don't pollute the cache during CI / capture stale entries. |
| `.gitignore` | Already covers `data/*.db` — verify, no change expected. |

## Config example

```yaml
llm:
  default_provider: anthropic
  default_model: claude-haiku-4-5
  cache_enabled: true                # default true
  cache_path: data/llm_cache.db      # default
  cache_ttl_hours: 24                # default 24h, 0 = forever
```

## Test plan

- **Unit (`test_llm_cache.py`):**
  - `test_cache_key_stable_across_dict_ordering` — same logical input,
    different dict iteration order → same key.
  - `test_cache_key_changes_with_temperature` — different temperatures
    → different keys.
  - `test_cache_key_strips_volatile_kwargs` — `stage="x"` vs `stage="y"`
    → same key (stage doesn't affect response).
  - `test_get_returns_none_on_miss`
  - `test_put_then_get_roundtrip`
  - `test_get_returns_none_after_ttl_expiry`
  - `test_purge_expired_deletes_old_rows`
  - `test_bypass_skips_read_and_write` (via the client wrapper test)
  - `test_wal_mode_enabled`
  - `test_concurrent_puts_dont_corrupt_db` (asyncio.gather × 20)

- **Integration (`test_llm_client_cache_integration.py`):**
  - First call to mocked LiteLLM: returns response, cache populated.
  - Second call with same input: doesn't call LiteLLM (assert mock
    call count == 1), returns cached response.
  - `cache=False` bypasses both ways.
  - Cache hit calls provenance collector with `cached=True`.

- **Provider-matrix test update:** all 7 liveness tests pass
  `cache=False`. Cache-on-by-default would make repeated CI runs
  bypass the real API and hide regressions.

## Rollout

- **Default-on.** The risk is low (worst case: stale response served;
  user re-runs with `cache=False`). The dev-iteration win is huge.
- No migration needed — `LLMResponseCache.__init__` creates the table
  with `IF NOT EXISTS`.
- No env-var gate — config-only.

## Followups (not in scope for v2.1)

- `cache_admin clear` CLI (Wave 2.4 grouping with budget caps).
- Per-provider TTL overrides (only needed if we see provider drift
  patterns).
- Cache statistics endpoint / Prometheus metric — log lines are
  enough for now; structured-log query covers it.
- LRU size cap — TTL is sufficient; if disk grows past 1 GB, revisit.
