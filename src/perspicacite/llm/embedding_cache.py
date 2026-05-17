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


import asyncio
import sqlite3
import time
from collections.abc import Iterable, Sequence
from pathlib import Path

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
