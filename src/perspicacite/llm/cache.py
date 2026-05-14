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
