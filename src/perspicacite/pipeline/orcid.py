"""Author name → ORCID disambiguation via OpenAlex (Wave 4.4).

Standalone resolver. Pure function semantics over a SQLite cache;
NEVER raises in the hot path — a network or API failure returns None
and logs a warning. Callers always need to handle the ``None`` case
either way.

See docs/superpowers/specs/2026-05-14-orcid-disambiguation-design.md.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.orcid")


@dataclass(frozen=True, slots=True)
class AuthorResolution:
    orcid: str
    display_name: str
    works_count: int
    confidence: float


_SCHEMA = """
CREATE TABLE IF NOT EXISTS orcid_cache (
    name          TEXT PRIMARY KEY,
    orcid         TEXT,
    display_name  TEXT,
    works_count   INTEGER,
    confidence    REAL,
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orcid_cache_created_at
    ON orcid_cache (created_at);
"""

_OPENALEX_AUTHORS = "https://api.openalex.org/authors"


class AuthorResolver:
    """Disambiguates author names to ORCID via OpenAlex with SQLite cache."""

    def __init__(
        self,
        *,
        cache_path: Path | str,
        ttl_days: int = 30,
        confidence_threshold: float = 0.20,
    ):
        self.cache_path = Path(cache_path)
        self.ttl_days = int(ttl_days)
        self.confidence_threshold = float(confidence_threshold)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.cache_path, check_same_thread=False, timeout=10.0)

    # ---- cache helpers -----------------------------------------------

    def _ttl_cutoff(self) -> int:
        if self.ttl_days <= 0:
            return 0
        return int(time.time()) - self.ttl_days * 86400

    def _cache_get(self, name: str) -> "AuthorResolution | None | _Sentinel":
        """Return ``AuthorResolution`` on hit, ``None`` on cached negative,
        or ``_MISS`` when there is no entry (or it expired)."""
        cutoff = self._ttl_cutoff()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT orcid, display_name, works_count, confidence, created_at "
                "FROM orcid_cache WHERE name = ?",
                (name,),
            ).fetchone()
            if row is None:
                return _MISS
            orcid, display, works, conf, created = row
            if created < cutoff:
                conn.execute("DELETE FROM orcid_cache WHERE name = ?", (name,))
                conn.commit()
                return _MISS
        if not orcid:
            return None
        return AuthorResolution(
            orcid=str(orcid),
            display_name=str(display or ""),
            works_count=int(works or 0),
            confidence=float(conf or 0.0),
        )

    def _cache_put(self, name: str, res: "AuthorResolution | None") -> None:
        with self._connect() as conn:
            if res is None:
                conn.execute(
                    "INSERT OR REPLACE INTO orcid_cache "
                    "(name, orcid, display_name, works_count, confidence, created_at) "
                    "VALUES (?, NULL, NULL, 0, 0.0, ?)",
                    (name, int(time.time())),
                )
            else:
                conn.execute(
                    "INSERT OR REPLACE INTO orcid_cache "
                    "(name, orcid, display_name, works_count, confidence, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, res.orcid, res.display_name, res.works_count,
                     res.confidence, int(time.time())),
                )
            conn.commit()

    # ---- HTTP --------------------------------------------------------

    async def _http_get(self, url: str) -> httpx.Response:
        """Thin wrapper so tests can patch this single seam."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url)

    # ---- parsing -----------------------------------------------------

    @staticmethod
    def _strip_orcid(s: "str | None") -> "str | None":
        if not s:
            return None
        s = s.strip()
        for prefix in ("https://orcid.org/", "http://orcid.org/"):
            if s.startswith(prefix):
                return s[len(prefix):]
        return s

    def _parse(self, body: str) -> "AuthorResolution | None":
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        results = payload.get("results") or []
        if not isinstance(results, list) or not results:
            return None
        top = results[0]
        top_orcid = self._strip_orcid(top.get("orcid"))
        if not top_orcid:
            return None
        top_works = int(top.get("works_count") or 0)
        second_works = 0
        if len(results) > 1:
            second_works = int(results[1].get("works_count") or 0)
        if top_works <= 0:
            return None
        spread = (top_works - second_works) / top_works
        if spread < self.confidence_threshold:
            return None
        return AuthorResolution(
            orcid=top_orcid,
            display_name=str(top.get("display_name") or ""),
            works_count=top_works,
            confidence=spread,
        )

    # ---- public API --------------------------------------------------

    async def resolve(self, name: str) -> "AuthorResolution | None":
        name = (name or "").strip()
        if not name:
            return None

        # Cache check (synchronously — SQLite is fast for KV lookups).
        hit = await asyncio.to_thread(self._cache_get, name)
        if hit is not _MISS:
            return hit  # type: ignore[return-value]  # _MISS sentinel filtered above

        # HTTP query.
        url = (
            _OPENALEX_AUTHORS
            + "?search=" + urllib.parse.quote(name)
            + "&per_page=5"
        )
        try:
            resp = await self._http_get(url)
        except Exception as exc:  # noqa: BLE001 — best-effort lookup
            logger.warning(
                "orcid_resolver_http_failed",
                name=name, error=str(exc), error_type=type(exc).__name__,
            )
            return None
        if resp.status_code != 200:
            logger.warning(
                "orcid_resolver_non_200", name=name, status=resp.status_code,
            )
            return None

        res = self._parse(resp.text)
        await asyncio.to_thread(self._cache_put, name, res)
        return res


class _Sentinel:
    """Marker for cache-miss (distinct from a cached negative)."""

    __slots__ = ()


_MISS: _Sentinel = _Sentinel()
