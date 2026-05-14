# ORCID disambiguation — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Standalone `AuthorResolver` that maps a name → ORCID via
OpenAlex with SQLite caching.

**Spec:** `docs/superpowers/specs/2026-05-14-orcid-disambiguation-design.md`

---

## Task 1: Config fields

**Files:**
- Modify: `src/perspicacite/config/schema.py` (`KnowledgeBaseConfig`)
- Test: `tests/unit/test_config_orcid_fields.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config_orcid_fields.py
"""Tests for ORCID resolver config fields (Wave 4.4)."""
from pathlib import Path

import pytest

from perspicacite.config.schema import KnowledgeBaseConfig


def test_orcid_defaults():
    kb = KnowledgeBaseConfig()
    assert kb.orcid_cache_path == Path("data/orcid_cache.db")
    assert kb.orcid_cache_ttl_days == 30
    assert kb.orcid_confidence_threshold == 0.20


def test_orcid_overrides():
    kb = KnowledgeBaseConfig(
        orcid_cache_path="custom/orcid.db",
        orcid_cache_ttl_days=7,
        orcid_confidence_threshold=0.5,
    )
    assert kb.orcid_cache_path == Path("custom/orcid.db")
    assert kb.orcid_cache_ttl_days == 7
    assert kb.orcid_confidence_threshold == 0.5


def test_orcid_threshold_bounded():
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(orcid_confidence_threshold=-0.1)
    with pytest.raises(pydantic.ValidationError):
        KnowledgeBaseConfig(orcid_confidence_threshold=1.5)
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Add the fields**

In `src/perspicacite/config/schema.py`, in `KnowledgeBaseConfig`,
after the `log_dir` field (Wave 4.3), add:

```python
    # ---- ORCID disambiguation (Wave 4.4) ---------------------------
    orcid_cache_path: Path = Field(
        default=Path("data/orcid_cache.db"),
        description=(
            "SQLite cache for name→ORCID resolutions. Covered by the "
            "data/*.db .gitignore rule."
        ),
    )
    orcid_cache_ttl_days: int = Field(
        default=30,
        ge=0,
        description="Days before a cached resolution expires. 0 = forever.",
    )
    orcid_confidence_threshold: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum (top1 - top2) / top1 spread between the best and "
            "second-best OpenAlex candidates. Below this, resolution "
            "returns None (ambiguous)."
        ),
    )
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_config_orcid_fields.py -v
pytest tests/integration/test_config_audit.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/config/schema.py \
        tests/unit/test_config_orcid_fields.py
git commit -m "feat(config): orcid_{cache_path,ttl_days,confidence_threshold} on KnowledgeBaseConfig (Wave 4.4)"
```

---

## Task 2: AuthorResolver module

**Files:**
- Create: `src/perspicacite/pipeline/orcid.py`
- Test: `tests/unit/test_orcid_resolver.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_orcid_resolver.py
"""Tests for AuthorResolver (Wave 4.4)."""
import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.orcid import AuthorResolution, AuthorResolver


def _mock_openalex_response(items: list[dict]) -> str:
    return json.dumps({"results": items})


@pytest.fixture
def resolver(tmp_path: Path) -> AuthorResolver:
    return AuthorResolver(
        cache_path=tmp_path / "orcid.db",
        ttl_days=30,
        confidence_threshold=0.20,
    )


def _author(name: str, orcid: str | None, works: int) -> dict:
    return {
        "display_name": name,
        "orcid": f"https://orcid.org/{orcid}" if orcid else None,
        "works_count": works,
        "id": f"https://openalex.org/A{abs(hash(name)) % 1_000_000}",
    }


@pytest.mark.asyncio
async def test_resolves_unambiguous_author(resolver):
    items = [
        _author("John Smith", "0000-0001-AAAA", works=200),
        _author("J. Smith", "0000-0002-BBBB", works=5),
    ]
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response(items),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("John Smith")
    assert res is not None
    assert res.orcid == "0000-0001-AAAA"
    assert res.display_name == "John Smith"
    assert res.works_count == 200
    assert res.confidence > 0.9   # 195/200


@pytest.mark.asyncio
async def test_returns_none_when_top_lacks_orcid(resolver):
    items = [
        _author("No-ORCID Author", None, works=200),
        _author("Other", "0000-0001-XXXX", works=10),
    ]
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response(items),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("Some Author")
    assert res is None


@pytest.mark.asyncio
async def test_returns_none_when_confidence_low(resolver):
    items = [
        _author("Author A", "0000-0001-AAAA", works=100),
        _author("Author B", "0000-0002-BBBB", works=95),  # spread=5%
    ]
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response(items),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("Ambiguous Author")
    assert res is None


@pytest.mark.asyncio
async def test_returns_none_when_results_empty(resolver):
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response([]),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("Nobody")
    assert res is None


@pytest.mark.asyncio
async def test_cache_hit_avoids_http(resolver):
    items = [_author("Hit Cache", "0000-0001-HIT", works=50)]
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response(items),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        await resolver.resolve("Hit Cache")
        await resolver.resolve("Hit Cache")
    # Second call must not hit the network.
    assert fake_get.call_count == 1


@pytest.mark.asyncio
async def test_cache_negative_avoids_http(resolver):
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response([]),
    ))
    with patch.object(resolver, "_http_get", new=fake_get):
        r1 = await resolver.resolve("Nope")
        r2 = await resolver.resolve("Nope")
    assert r1 is None and r2 is None
    assert fake_get.call_count == 1


@pytest.mark.asyncio
async def test_ttl_expiry_re_queries(tmp_path):
    resolver = AuthorResolver(
        cache_path=tmp_path / "orcid.db",
        ttl_days=1,
        confidence_threshold=0.20,
    )
    items = [_author("TTL Test", "0000-0001-TTL", works=30)]
    fake_get = AsyncMock(return_value=MagicMock(
        status_code=200, text=_mock_openalex_response(items),
    ))
    # Seed the cache with a row dated 2 days ago.
    with sqlite3.connect(tmp_path / "orcid.db") as conn:
        conn.execute(
            "INSERT INTO orcid_cache "
            "(name, orcid, display_name, works_count, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TTL Test", "0000-0001-OLD", "Old", 1, 1.0,
             int(time.time()) - 2 * 86400),
        )
        conn.commit()
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("TTL Test")
    assert fake_get.call_count == 1   # re-queried
    assert res is not None
    assert res.orcid == "0000-0001-TTL"  # fresh value, not the old one


@pytest.mark.asyncio
async def test_blank_name_returns_none(resolver):
    with patch.object(resolver, "_http_get") as fake_get:
        assert await resolver.resolve("") is None
        assert await resolver.resolve("   ") is None
        fake_get.assert_not_called()


@pytest.mark.asyncio
async def test_network_failure_returns_none(resolver):
    fake_get = AsyncMock(side_effect=ConnectionError("dns lookup failed"))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("Network Down")
    assert res is None


@pytest.mark.asyncio
async def test_non_200_returns_none(resolver):
    fake_get = AsyncMock(return_value=MagicMock(status_code=500, text=""))
    with patch.object(resolver, "_http_get", new=fake_get):
        res = await resolver.resolve("Server Down")
    assert res is None


def test_strips_orcid_url_prefix(resolver):
    """Internal helper: ``https://orcid.org/0000-...`` → ``0000-...``"""
    assert resolver._strip_orcid("https://orcid.org/0000-0001-XYZW") == "0000-0001-XYZW"
    assert resolver._strip_orcid("http://orcid.org/0000-0001-AAAA") == "0000-0001-AAAA"
    assert resolver._strip_orcid("0000-0001-RAW") == "0000-0001-RAW"
    assert resolver._strip_orcid(None) is None
```

- [ ] **Step 2: Run, watch fail**

- [ ] **Step 3: Implement**

Create `src/perspicacite/pipeline/orcid.py`:

```python
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

    def _cache_get(self, name: str) -> AuthorResolution | None | _Sentinel:
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

    def _cache_put(self, name: str, res: AuthorResolution | None) -> None:
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
    def _strip_orcid(s: str | None) -> str | None:
        if not s:
            return None
        s = s.strip()
        for prefix in ("https://orcid.org/", "http://orcid.org/"):
            if s.startswith(prefix):
                return s[len(prefix):]
        return s

    def _parse(self, body: str) -> AuthorResolution | None:
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

    async def resolve(self, name: str) -> AuthorResolution | None:
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
```

- [ ] **Step 4: Run, watch pass**

```bash
pytest tests/unit/test_orcid_resolver.py -v
```

Expected: 11 PASSED.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/pipeline/orcid.py \
        tests/unit/test_orcid_resolver.py
git commit -m "feat(orcid): AuthorResolver — OpenAlex disambiguation + SQLite cache (Wave 4.4)"
```

---

## Task 3: Operator doc

**Files:**
- Create: `docs/orcid-disambiguation-2026-05-14.md`
- Modify: `.gitignore`

- [ ] **Step 1: Write the doc**

```markdown
# ORCID disambiguation — operator guide (2026-05-14)

Wave 4.4 of the framework-hardening roadmap. Map free-text author
names to canonical ORCID IDs via OpenAlex.

## API

```python
from pathlib import Path
from perspicacite.pipeline.orcid import AuthorResolver

resolver = AuthorResolver(
    cache_path=Path("data/orcid_cache.db"),
    ttl_days=30,
    confidence_threshold=0.20,
)
res = await resolver.resolve("Smith J.")
if res is not None:
    print(res.orcid, res.display_name, res.confidence)
```

`AuthorResolution` fields:

| Field | Meaning |
|---|---|
| `orcid` | `"0000-0001-..."` (URL prefix stripped) |
| `display_name` | OpenAlex's canonical display name |
| `works_count` | Number of works in OpenAlex |
| `confidence` | `(top1 - top2) / top1` — works-count spread |

`None` returns when:

- Name is blank.
- OpenAlex returns no results.
- Top result has no ORCID.
- Confidence < `confidence_threshold` (ambiguous).
- Network / HTTP failure (logged, not raised).

## Confidence threshold tuning

The default `0.20` accepts most reasonable matches and rejects
genuinely ambiguous ones (e.g., two "J. Smith" with similar
publication counts). Raise to `0.50` for high-precision use cases
(citations, ground-truth labels); lower to `0.10` for high-recall
exploratory work.

## Caching

A SQLite cache at `data/orcid_cache.db` stores every resolution —
positive and negative — for 30 days by default. Negative entries
prevent the resolver from hammering OpenAlex on names that don't
disambiguate.

```yaml
kb:
  orcid_cache_path: data/orcid_cache.db
  orcid_cache_ttl_days: 30           # 0 = forever
  orcid_confidence_threshold: 0.20
```

Manual cache clear:

```bash
rm data/orcid_cache.db
```

Selective by name:

```bash
sqlite3 data/orcid_cache.db \
  "DELETE FROM orcid_cache WHERE name LIKE 'J. Smith%';"
```

## Scope today

- **Module is wired**: `pipeline/orcid.py` resolves on demand.
- **Not wired into ingest**: today's `ingest_dois_into_kb` doesn't
  call the resolver. Wiring is mechanical (~20 lines per ingest
  path) and lives in a separate follow-up so this PR stays focused.

## API rate limits

OpenAlex's public API requires no auth and allows generous traffic
(no documented per-IP limit at small scale). The resolver respects
the suggested polite-pool conventions by:

- Setting `User-Agent` via httpx defaults (no custom override yet).
- Caching aggressively so we never re-query the same name within 30
  days.

For production-grade traffic, add a `mailto=...` query param. That's
a documented followup.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/orcid.py` | `AuthorResolver`, `AuthorResolution` |
| `src/perspicacite/config/schema.py` | `orcid_*` fields on `KnowledgeBaseConfig` |

## Followups

- Wire into ingest (Paper authors get `orcid` stamped automatically).
- Bulk endpoint (batch 25 names per OpenAlex call).
- ORCID API as a secondary lookup with auth.
- Affiliation-context disambiguation.
- Add `mailto=` for the OpenAlex polite-pool.
```

- [ ] **Step 2: Allowlist the doc**

Add `!docs/orcid-disambiguation-*.md` to `.gitignore` after
`!docs/versioned-kbs-*.md`.

- [ ] **Step 3: Commit**

```bash
git add docs/orcid-disambiguation-2026-05-14.md .gitignore
git commit -m "docs(orcid): operator guide (Wave 4.4)"
```

---

## Done

After Task 3:

- New `AuthorResolver` module (~200 LoC).
- Three new config fields on `KnowledgeBaseConfig`.
- 14 new tests covering parsing, caching, TTL, error paths.
- Operator doc landed.
- Ingest-wiring is a documented follow-up.
