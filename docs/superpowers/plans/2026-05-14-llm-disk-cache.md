# LLM disk cache — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache `AsyncLLMClient.complete()` responses to a SQLite file
keyed by `(provider, model, messages, temperature, max_tokens, ...)`.
TTL-based invalidation, per-call bypass, provenance-integrated.

**Architecture:** New `LLMResponseCache` class in `llm/cache.py` owns
the SQLite connection; `AsyncLLMClient` composes it and calls
`get()` / `put()` around the existing dispatch path. Config-driven
TTL and on/off knob in `LLMConfig`.

**Tech stack:** stdlib `sqlite3` (sync API used from async code via
`asyncio.to_thread`), `hashlib.sha256`, `json` for canonical
serialisation. No new third-party deps.

**Spec:** `docs/superpowers/specs/2026-05-14-llm-disk-cache-design.md`

---

## Task 1: Config schema additions

**Files:**
- Modify: `src/perspicacite/config/schema.py:222-281` (the `LLMConfig` class)
- Test: `tests/unit/test_config_llm_cache_fields.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_llm_cache_fields.py
"""Tests for the cache-related LLMConfig fields (Wave 2.1)."""
from pathlib import Path

from perspicacite.config.schema import LLMConfig


def test_llm_cache_defaults_are_sensible():
    """Cache should be enabled by default with 24h TTL.

    Rationale: the dev-iteration win is huge and the worst-case
    failure (stale response) is easy to spot and bypass per-call.
    Default-on prevents users from forgetting to enable it and missing
    the speedup.
    """
    cfg = LLMConfig()
    assert cfg.cache_enabled is True
    assert cfg.cache_path == Path("data/llm_cache.db")
    assert cfg.cache_ttl_hours == 24


def test_llm_cache_can_be_disabled():
    cfg = LLMConfig(cache_enabled=False)
    assert cfg.cache_enabled is False


def test_llm_cache_ttl_zero_means_forever():
    """TTL=0 is the documented sentinel for 'never expire'."""
    cfg = LLMConfig(cache_ttl_hours=0)
    assert cfg.cache_ttl_hours == 0


def test_llm_cache_path_accepts_string_and_path():
    """Pydantic should coerce a YAML string into Path."""
    cfg = LLMConfig(cache_path="some/other/path.db")  # type: ignore[arg-type]
    assert cfg.cache_path == Path("some/other/path.db")
```

- [ ] **Step 2: Run the test, verify it fails**

```bash
pytest tests/unit/test_config_llm_cache_fields.py -v
```

Expected: 4 failures with `AttributeError: 'LLMConfig' object has no attribute 'cache_enabled'`.

- [ ] **Step 3: Add the three fields to LLMConfig**

In `src/perspicacite/config/schema.py`, locate the `LLMConfig` class
(starts at line 222). Add `from pathlib import Path` to the imports
at the top of the file if not already imported (it is, used elsewhere).

After the existing `use_mcp_sampling` field (around line 281) but
before `providers:`, add:

```python
    # ---- disk cache (Wave 2.1) -------------------------------------
    # Cache complete() responses on disk keyed by
    # (provider, model, messages, temperature, max_tokens). Pays back
    # on every dev iteration and on slow agent-CLI paths (6–16 s →
    # <10 ms). See docs/superpowers/specs/2026-05-14-llm-disk-cache-design.md.
    cache_enabled: bool = Field(
        default=True,
        description=(
            "Cache LLM responses on disk so repeated identical calls "
            "return instantly. Default on; bypass per-call with "
            "client.complete(..., cache=False)."
        ),
    )
    cache_path: Path = Field(
        default=Path("data/llm_cache.db"),
        description=(
            "SQLite file backing the cache. Created on first use. "
            "Already covered by the `data/*.db` .gitignore rule."
        ),
    )
    cache_ttl_hours: int = Field(
        default=24,
        ge=0,
        description=(
            "Cached responses expire after this many hours. 0 means "
            "never expire (kept until manually cleared)."
        ),
    )
```

- [ ] **Step 4: Run the tests, verify they pass**

```bash
pytest tests/unit/test_config_llm_cache_fields.py -v
```

Expected: 4 PASSED.

Also run the existing config audit to ensure nothing broke:

```bash
pytest tests/integration/test_config_audit.py -v
```

Expected: 12 PASSED (or however many existed before — number unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py tests/unit/test_config_llm_cache_fields.py
git commit -m "feat(config): cache_enabled/path/ttl_hours fields on LLMConfig (Wave 2.1)"
```

---

## Task 2: LLMResponseCache — key + serialisation

**Files:**
- Create: `src/perspicacite/llm/cache.py`
- Test: `tests/unit/test_llm_cache_key.py`

Implements only the pure-function pieces first (key building, JSON
canonicalisation). The SQLite layer comes in Task 3.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_llm_cache_key.py
"""Tests for cache-key stability and isolation (Wave 2.1)."""
import pytest

from perspicacite.llm.cache import build_cache_key


def _msg(role: str, content: str) -> dict:
    return {"role": role, "content": content}


def test_key_stable_across_calls():
    """Same input → same key. Sanity check."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")],
        temperature=0.0, max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")],
        temperature=0.0, max_tokens=100, extra_kwargs={},
    )
    assert k1 == k2
    assert len(k1) == 64  # SHA256 hex digest


def test_key_differs_on_provider():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="openai", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_model():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-sonnet-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_messages():
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hello")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_differs_on_temperature():
    """Temperature must participate — temp=0 and temp=0.7 are
    semantically different calls."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100, extra_kwargs={},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.7,
        max_tokens=100, extra_kwargs={},
    )
    assert k1 != k2


def test_key_strips_volatile_kwargs():
    """`stage`, `cache`, `timeout` don't affect what the provider
    returns; they shouldn't pollute the key."""
    k1 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"stage": "routing", "cache": True, "timeout": 30},
    )
    k2 = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"stage": "screening", "cache": False, "timeout": 60},
    )
    assert k1 == k2


def test_key_includes_non_volatile_kwargs():
    """response_format / tools / etc. DO affect the result, so they
    must end up in the key."""
    k1 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={"response_format": {"type": "json_object"}},
    )
    k2 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[_msg("user", "hi")], temperature=0.0,
        max_tokens=100,
        extra_kwargs={},
    )
    assert k1 != k2


def test_key_stable_across_dict_ordering():
    """Python dicts preserve insertion order; the key shouldn't."""
    k1 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0, max_tokens=100,
        extra_kwargs={"a": 1, "b": 2},
    )
    k2 = build_cache_key(
        provider="openai", model="gpt-4o-mini",
        messages=[{"content": "hi", "role": "user"}],
        temperature=0.0, max_tokens=100,
        extra_kwargs={"b": 2, "a": 1},
    )
    assert k1 == k2


def test_key_handles_anthropic_content_blocks():
    """Anthropic messages can have list-of-typed-blocks content.
    The serialiser must walk them without choking."""
    messages = [
        {"role": "system", "content": [{"type": "text", "text": "be helpful"}]},
        {"role": "user", "content": [
            {"type": "text", "text": "context", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "question"},
        ]},
    ]
    k = build_cache_key(
        provider="anthropic", model="claude-haiku-4-5",
        messages=messages, temperature=0.0, max_tokens=100,
        extra_kwargs={},
    )
    assert len(k) == 64
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/unit/test_llm_cache_key.py -v
```

Expected: `ModuleNotFoundError: No module named 'perspicacite.llm.cache'`.

- [ ] **Step 3: Implement `build_cache_key`**

Create `src/perspicacite/llm/cache.py`:

```python
"""On-disk SQLite cache for AsyncLLMClient.complete() responses.

See docs/superpowers/specs/2026-05-14-llm-disk-cache-design.md for
the design rationale. This module is intentionally narrow: pure key
building + a thin sqlite3 wrapper. AsyncLLMClient composes it in
client.py.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# Kwargs that don't affect the model's output and therefore must not
# participate in the cache key. Adding to this list is backwards
# compatible (only widens hits). Removing requires bumping a key
# version, which would invalidate the cache.
_VOLATILE_KWARGS: frozenset[str] = frozenset({
    "stage",      # provenance label only
    "cache",      # the cache-bypass flag itself
    "timeout",    # affects whether a call succeeds, not what it returns
})


def _canonical(obj: Any) -> Any:
    """Coerce ``obj`` into a form whose JSON serialisation is stable
    across Python dict-ordering. Recursive."""
    if isinstance(obj, dict):
        return {k: _canonical(obj[k]) for k in sorted(obj)}
    if isinstance(obj, (list, tuple)):
        return [_canonical(x) for x in obj]
    return obj


def build_cache_key(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    temperature: float,
    max_tokens: int,
    extra_kwargs: dict[str, Any],
) -> str:
    """Compute the SHA256 cache key for an LLM call.

    The key is stable across:
    - Python dict insertion order (we canonicalise before serialising).
    - Volatile-kwarg values (``stage``, ``cache``, ``timeout``).

    The key changes whenever any field that meaningfully affects the
    provider's response changes (provider, model, messages, temperature,
    max_tokens, response_format, tools, ...).
    """
    filtered = {k: v for k, v in extra_kwargs.items() if k not in _VOLATILE_KWARGS}
    payload = {
        "provider": provider,
        "model": model,
        "messages": _canonical(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "extra": _canonical(filtered),
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/unit/test_llm_cache_key.py -v
```

Expected: 9 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/cache.py tests/unit/test_llm_cache_key.py
git commit -m "feat(llm-cache): canonical cache-key builder (Wave 2.1)"
```

---

## Task 3: LLMResponseCache — SQLite layer

**Files:**
- Modify: `src/perspicacite/llm/cache.py` (append the `LLMResponseCache` class)
- Test: `tests/unit/test_llm_cache_storage.py`

- [ ] **Step 1: Write the failing tests**

```python
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
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/unit/test_llm_cache_storage.py -v
```

Expected: 8 failures, mostly `ImportError: cannot import name 'LLMResponseCache'`.

- [ ] **Step 3: Implement `LLMResponseCache`**

Append to `src/perspicacite/llm/cache.py`:

```python
import asyncio
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """A row read out of the LLM cache."""
    response: str
    provider: str
    model: str
    created_at: int
    latency_ms: float
    input_tokens: int
    output_tokens: int


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    key            TEXT PRIMARY KEY,
    provider       TEXT NOT NULL,
    model          TEXT NOT NULL,
    response       TEXT NOT NULL,
    created_at     INTEGER NOT NULL,
    latency_ms     REAL,
    input_tokens   INTEGER,
    output_tokens  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_created_at
    ON llm_cache (created_at);
"""


class LLMResponseCache:
    """SQLite-backed cache for ``AsyncLLMClient.complete()`` responses.

    Thread-/async-safe via short connections per operation + WAL mode.
    Reads return ``None`` on miss or expiry. Lazy GC: expired rows are
    deleted on read; full sweep available via :meth:`purge_expired`.
    """

    def __init__(self, path: Path | str, ttl_hours: int = 24) -> None:
        self.path = Path(path)
        self.ttl_hours = int(ttl_hours)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Initialise schema + pragmas synchronously (cheap, only at startup).
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.commit()

    # ---- low-level helpers ------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # check_same_thread=False because asyncio.to_thread can hand the
        # connection off to a worker thread different from the opener.
        # Short-lived connections per call keep the locking surface small.
        return sqlite3.connect(self.path, check_same_thread=False, timeout=10.0)

    def _ttl_cutoff(self) -> int:
        """Returns the unix-second below which rows are considered expired.
        When ``ttl_hours == 0`` we treat the cache as eternal — return 0
        so no row ever falls below the cutoff."""
        if self.ttl_hours <= 0:
            return 0
        return int(time.time()) - self.ttl_hours * 3600

    # ---- public API -------------------------------------------------------

    async def get(self, key: str) -> CachedResponse | None:
        return await asyncio.to_thread(self._get_sync, key)

    def _get_sync(self, key: str) -> CachedResponse | None:
        cutoff = self._ttl_cutoff()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response, provider, model, created_at, latency_ms, "
                "input_tokens, output_tokens FROM llm_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            (response, provider, model, created_at,
             latency_ms, input_tokens, output_tokens) = row
            if created_at < cutoff:
                # Lazy GC for this one row.
                conn.execute("DELETE FROM llm_cache WHERE key = ?", (key,))
                conn.commit()
                return None
        return CachedResponse(
            response=response,
            provider=provider,
            model=model,
            created_at=int(created_at),
            latency_ms=float(latency_ms) if latency_ms is not None else 0.0,
            input_tokens=int(input_tokens) if input_tokens is not None else 0,
            output_tokens=int(output_tokens) if output_tokens is not None else 0,
        )

    async def put(
        self,
        *,
        key: str,
        provider: str,
        model: str,
        response: str,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        _created_at_override: int | None = None,
    ) -> None:
        await asyncio.to_thread(
            self._put_sync, key, provider, model, response,
            latency_ms, input_tokens, output_tokens, _created_at_override,
        )

    def _put_sync(
        self,
        key: str,
        provider: str,
        model: str,
        response: str,
        latency_ms: float,
        input_tokens: int,
        output_tokens: int,
        created_at_override: int | None,
    ) -> None:
        created_at = (
            int(created_at_override)
            if created_at_override is not None
            else int(time.time())
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(key, provider, model, response, created_at, "
                " latency_ms, input_tokens, output_tokens) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (key, provider, model, response, created_at,
                 latency_ms, input_tokens, output_tokens),
            )
            conn.commit()

    async def purge_expired(self) -> int:
        """Delete all expired rows. Returns the number purged.

        Idempotent. Safe to call on every process startup."""
        return await asyncio.to_thread(self._purge_expired_sync)

    def _purge_expired_sync(self) -> int:
        cutoff = self._ttl_cutoff()
        if cutoff == 0:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM llm_cache WHERE created_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount or 0
```

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/unit/test_llm_cache_storage.py -v
```

Expected: 8 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/llm/cache.py tests/unit/test_llm_cache_storage.py
git commit -m "feat(llm-cache): SQLite storage layer with TTL + WAL (Wave 2.1)"
```

---

## Task 4: Wire cache into AsyncLLMClient.complete()

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_llm_client_cache_integration.py` (new)

- [ ] **Step 1: Write the failing integration tests**

```python
# tests/unit/test_llm_client_cache_integration.py
"""End-to-end tests for AsyncLLMClient ↔ LLMResponseCache wiring (Wave 2.1)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.client import AsyncLLMClient


def _mk_config(tmp_path: Path, *, enabled: bool = True) -> LLMConfig:
    """Build an LLMConfig pointing the cache at a tmp file."""
    return LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=enabled,
        cache_path=tmp_path / "test_cache.db",
        cache_ttl_hours=24,
    )


def _mock_litellm_response(text: str):
    """Build a fake LiteLLM response object."""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    response = MagicMock()
    response.choices = [choice]
    response.get = MagicMock(
        side_effect=lambda k, d=None: {"usage": {"prompt_tokens": 10,
                                                  "completion_tokens": 5}}.get(k, d)
    )
    return response


@pytest.mark.asyncio
async def test_first_call_hits_provider_second_call_returns_cached(tmp_path):
    """The core invariant: re-asking the same prompt skips the network."""
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("hello!"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        r1 = await client.complete(messages=messages, temperature=0.0)
        r2 = await client.complete(messages=messages, temperature=0.0)

    assert r1 == "hello!"
    assert r2 == "hello!"
    # The provider was only called once — the second call was served
    # from cache.
    assert fake_acompletion.call_count == 1


@pytest.mark.asyncio
async def test_cache_false_bypasses_both_read_and_write(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("uncached"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        await client.complete(messages=messages, temperature=0.0, cache=False)
        await client.complete(messages=messages, temperature=0.0, cache=False)

    # Both calls hit the provider because the cache was bypassed.
    assert fake_acompletion.call_count == 2


@pytest.mark.asyncio
async def test_cache_disabled_globally_doesnt_touch_db(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path, enabled=False))
    messages = [{"role": "user", "content": "hi"}]

    fake_acompletion = AsyncMock(return_value=_mock_litellm_response("nope"))
    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = fake_acompletion
        mock_get.return_value = mock_litellm

        await client.complete(messages=messages, temperature=0.0)
        await client.complete(messages=messages, temperature=0.0)

    # No cache → both calls hit the provider.
    assert fake_acompletion.call_count == 2
    # The DB file should not have been created when cache_enabled=False.
    assert not (tmp_path / "test_cache.db").exists()


@pytest.mark.asyncio
async def test_different_temperatures_are_separate_entries(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    messages = [{"role": "user", "content": "hi"}]

    responses = iter(["t0", "t07"])

    async def fake_call(*args, **kwargs):
        return _mock_litellm_response(next(responses))

    with patch.object(client, "_get_litellm") as mock_get:
        mock_litellm = MagicMock()
        mock_litellm.acompletion = AsyncMock(side_effect=fake_call)
        mock_get.return_value = mock_litellm

        r1 = await client.complete(messages=messages, temperature=0.0)
        r2 = await client.complete(messages=messages, temperature=0.7)

    assert r1 == "t0"
    assert r2 == "t07"
```

- [ ] **Step 2: Run tests, verify they fail**

```bash
pytest tests/unit/test_llm_client_cache_integration.py -v
```

Expected: most tests fail because the cache isn't wired yet — every
call will go to the provider, `call_count` will be 2 instead of 1.

- [ ] **Step 3: Wire the cache into `AsyncLLMClient`**

In `src/perspicacite/llm/client.py`:

**3a.** Top of file, add the import:

```python
from perspicacite.llm.cache import LLMResponseCache, build_cache_key
```

**3b.** In `AsyncLLMClient.__init__` (around line 158-163), after the
`self._agent_clis = {}` line, add:

```python
        # Disk cache (Wave 2.1). Constructed lazily on first access so
        # callers that disable caching never touch the filesystem.
        self._cache: LLMResponseCache | None = None
```

**3c.** Add a helper method just below `__init__`:

```python
    def _get_cache(self) -> LLMResponseCache | None:
        """Lazy-init the disk cache. Returns None when disabled."""
        if not getattr(self.config, "cache_enabled", False):
            return None
        if self._cache is None:
            self._cache = LLMResponseCache(
                path=self.config.cache_path,
                ttl_hours=self.config.cache_ttl_hours,
            )
        return self._cache
```

**3d.** In `complete()` (around line 276), right after the
`if model is None: model = ...` block and *before* the MCP-sampling
branch, insert the cache-read:

```python
        # ---- disk cache lookup (Wave 2.1) -----------------------------
        # Cache key is computed from the resolved (provider, model)
        # pair plus everything that affects the response. Volatile
        # kwargs (stage, cache, timeout) are filtered inside
        # build_cache_key.
        cache_bypass = kwargs.pop("cache", True) is False
        cache = None if cache_bypass else self._get_cache()
        cache_key: str | None = None
        if cache is not None:
            cache_key = build_cache_key(
                provider=provider, model=model,
                messages=messages, temperature=temperature,
                max_tokens=max_tokens, extra_kwargs=kwargs,
            )
            hit = await cache.get(cache_key)
            if hit is not None:
                logger.info(
                    "llm_cache_hit",
                    stage=stage_label, provider=provider, model=model,
                    age_seconds=int(time.time()) - hit.created_at,
                )
                from perspicacite.provenance.context import get_collector
                _c = get_collector()
                if _c is not None:
                    _c.add_llm_call(
                        stage_label=stage_label,
                        provider=provider,
                        model=model,
                        prompt_messages=messages,
                        response_text=hit.response,
                        prompt_tokens=hit.input_tokens,
                        completion_tokens=hit.output_tokens,
                        latency_ms=hit.latency_ms,
                    )
                return hit.response
            logger.debug(
                "llm_cache_miss",
                stage=stage_label, provider=provider, model=model,
            )
```

**3e.** Wrap the **two** existing success paths to write to the cache.
After the `return content` at the end of the Minimax branch (around
line 410), and after the `return content` at the end of the standard
branch (around line 443), insert *before each* return:

```python
            if cache is not None and cache_key is not None:
                await cache.put(
                    key=cache_key, provider=provider, model=model,
                    response=content or "", latency_ms=latency_ms,
                    input_tokens=int(usage.get("prompt_tokens", 0) or 0),
                    output_tokens=int(usage.get("completion_tokens", 0) or 0),
                )
```

For the agent-CLI branch (around line 330-336), the return is from
`cli.complete(...)` — we have no `usage` dict from that path, so cache
with zeros:

```python
        if self._is_agent_cli_provider(provider):
            cli = self._get_agent_cli_client(provider)
            content = await cli.complete(
                messages=messages, model=model, provider=provider,
                temperature=temperature, max_tokens=max_tokens,
                stage=stage_label, **kwargs,
            )
            if cache is not None and cache_key is not None:
                await cache.put(
                    key=cache_key, provider=provider, model=model,
                    response=content, latency_ms=0.0,
                    input_tokens=0, output_tokens=0,
                )
            return content
```

(Replace the existing `return await cli.complete(...)` with the block
above so the cache write happens.)

- [ ] **Step 4: Run tests, verify they pass**

```bash
pytest tests/unit/test_llm_client_cache_integration.py -v
pytest tests/unit/test_llm_cache_key.py tests/unit/test_llm_cache_storage.py -v
```

Expected: all PASSED.

- [ ] **Step 5: Verify no regressions in the broader unit suite**

```bash
pytest tests/unit/ \
  --ignore=tests/unit/test_embeddings.py \
  --ignore=tests/unit/test_capsule_builder_orchestrator.py \
  --ignore=tests/unit/test_fetch_doi_lookups.py \
  --timeout=15 --timeout-method=signal \
  -q --no-header --tb=line 2>&1 | tail -20
```

Expected: same pass/fail count as the Wave 1.1 baseline (869 passing,
12 failing — none of the 12 should be a new failure introduced here).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_llm_client_cache_integration.py
git commit -m "feat(llm-cache): wire cache read/write into AsyncLLMClient.complete (Wave 2.1)"
```

---

## Task 5: Update provider-matrix tests to bypass cache

**Files:**
- Modify: `tests/integration/test_provider_matrix.py`

The liveness tests call real providers; caching their responses would
mean future CI runs serve stale answers and miss regressions.

- [ ] **Step 1: Locate the live calls**

Open `tests/integration/test_provider_matrix.py`. Find each
`client.complete(...)` call inside a `@pytest.mark.live` test.

- [ ] **Step 2: Add `cache=False` to each live call**

For every `await client.complete(messages=..., model=..., ...)` call
inside a live test, add `cache=False`. Example:

```python
result = await client.complete(
    messages=[{"role": "user", "content": "ping"}],
    model=cfg.default_model,
    provider=provider_name,
    temperature=0.0,
    max_tokens=20,
    cache=False,           # Wave 2.1: never cache liveness responses
)
```

- [ ] **Step 3: Run the provider-matrix tests**

```bash
pytest tests/integration/test_provider_matrix.py -v --no-header \
  --timeout=60 --timeout-method=signal
```

Expected: same outcome as Wave 1.2 baseline (10 pass, 5 skipped, 0 fail).

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_provider_matrix.py
git commit -m "test(provider-matrix): cache=False on liveness calls (Wave 2.1)"
```

---

## Task 6: Provider-cached responses status doc

**Files:**
- Create: `docs/llm-cache-2026-05-14.md`

Self-contained status note so future contributors understand what the
cache does, how to bypass it, and how to clear it manually.

- [ ] **Step 1: Write the doc**

```markdown
# LLM disk cache — status & operator guide (2026-05-14)

Wave 2.1 of the framework-hardening roadmap. Disk-cached LLM
responses for `AsyncLLMClient.complete()`.

## What it does

Every `await client.complete(...)` call that returns successfully
writes its response into `data/llm_cache.db` keyed by SHA256 of
`(provider, model, messages, temperature, max_tokens, response-shaping kwargs)`.
A subsequent call with identical inputs returns from the SQLite file in
<10 ms instead of hitting the provider.

## When it pays off

- **Re-running tests / debugging prompts**: same prompt, same answer,
  no API charge.
- **Agent-CLI paths**: a 6–16 s Codex / Claude Code round-trip
  collapses to a single SQLite read.
- **Multi-stage pipelines**: when a single user query triggers
  routing + screening + rephrase + retrieval + synthesis, the first
  three are typically deterministic and rarely change between runs —
  perfect cache fits.

## When to bypass

```python
await client.complete(..., cache=False)
```

- Liveness / integration tests that need genuine round-trips.
- High-temperature draws where you actually want a fresh sample.
- Debugging "is this provider drift or my code?" — bypass once, see
  whether the answer changes.

## Configuration

```yaml
llm:
  cache_enabled: true               # default true; false disables read+write
  cache_path: data/llm_cache.db     # default; SQLite file
  cache_ttl_hours: 24               # default 24h; 0 = never expire
```

## Clearing the cache

There is no admin CLI yet (followup). To clear manually:

```bash
rm data/llm_cache.db data/llm_cache.db-shm data/llm_cache.db-wal
```

Or selectively, via SQLite:

```bash
sqlite3 data/llm_cache.db "DELETE FROM llm_cache WHERE provider = 'anthropic';"
```

## What is NOT cached

- `stream()` calls — chunked output is rare in this codebase and the
  deterministic-replay value is low.
- Embedding calls — covered by Wave 2.2 separately.
- Anthropic server-side ephemeral cache_control — that's a separate
  optimisation layer; the two compose (disk cache hits *before* HTTP).

## Provenance integration

Cache hits still appear in the provenance collector with the original
`latency_ms` and `input_tokens` / `output_tokens`. They are
indistinguishable from a fresh call in the trace — except they
happened in <10 ms. (A future Wave 2.4 budget-caps pass may add an
explicit `cached: True` flag so cost accounting can skip them.)

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/llm/cache.py` | `LLMResponseCache` + `build_cache_key` |
| `src/perspicacite/llm/client.py` | Wiring into `AsyncLLMClient.complete` |
| `src/perspicacite/config/schema.py` | `cache_enabled`, `cache_path`, `cache_ttl_hours` |
| `tests/unit/test_llm_cache_key.py` | Key-stability tests |
| `tests/unit/test_llm_cache_storage.py` | SQLite layer tests |
| `tests/unit/test_llm_client_cache_integration.py` | End-to-end wiring tests |

## Open followups

- Per-provider TTL overrides (only if drift patterns warrant).
- `cache_admin` CLI (grouped with Wave 2.4 budget caps).
- LRU size cap (only if disk grows past 1 GB).
- Cache `stream()` for completeness (low priority).
```

- [ ] **Step 2: Allow the doc in .gitignore**

Add `!docs/llm-cache-*.md` to the documentation allowlist section
in `.gitignore` (after the `!docs/ci-setup-*.md` line):

```
!docs/llm-cache-*.md
```

- [ ] **Step 3: Commit**

```bash
git add docs/llm-cache-2026-05-14.md .gitignore
git commit -m "docs(llm-cache): operator guide for the disk cache (Wave 2.1)"
```

---

## Done

After Task 6 commits:

- New module `src/perspicacite/llm/cache.py` (~150 LoC).
- Three new fields on `LLMConfig`.
- `AsyncLLMClient.complete()` consults the cache transparently.
- Liveness tests opt out so they don't pollute the cache.
- 21 new unit/integration tests, all passing.
- Operator doc landed.
- Total ~4 commits + 2 helper commits = 6 commits, all under 200 LoC each.

No regression in the 869-passing baseline; the chunking-hang and
fixture-drift failures from Wave 1.1 are untouched.
