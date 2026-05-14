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
