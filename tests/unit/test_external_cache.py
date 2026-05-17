import json
import time

from perspicacite.pipeline.external.cache import (
    cache_load,
    cache_path,
    cache_store,
)


def test_cache_path_is_deterministic(tmp_path):
    p1 = cache_path(tmp_path, "crossref", "10.1/x")
    p2 = cache_path(tmp_path, "crossref", "10.1/x")
    assert p1 == p2
    assert p1.parent == tmp_path
    assert "crossref__" in p1.name


def test_cache_path_differs_by_query(tmp_path):
    a = cache_path(tmp_path, "crossref", "q1")
    b = cache_path(tmp_path, "crossref", "q2")
    assert a != b


def test_store_then_load_roundtrip(tmp_path):
    p = cache_path(tmp_path, "crossref", "q1")
    cache_store(p, {"hello": "world"})
    loaded = cache_load(p, ttl_seconds=3600)
    assert loaded == {"hello": "world"}


def test_load_missing_returns_none(tmp_path):
    p = cache_path(tmp_path, "x", "q")
    assert cache_load(p, ttl_seconds=3600) is None


def test_load_corrupt_purges(tmp_path):
    p = cache_path(tmp_path, "x", "q")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{not valid json")
    assert cache_load(p, ttl_seconds=3600) is None
    assert not p.exists()


def test_ttl_expiry_purges(tmp_path):
    p = cache_path(tmp_path, "x", "q")
    cache_store(p, {"v": 1})
    # Force timestamp into the distant past
    raw = json.loads(p.read_text())
    raw["_cached_at"] = time.time() - 999_999
    p.write_text(json.dumps(raw))
    assert cache_load(p, ttl_seconds=3600) is None
    assert not p.exists()


def test_legacy_unwrapped_payload_is_accepted(tmp_path):
    p = cache_path(tmp_path, "x", "q")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"raw": "no wrapper"}))
    assert cache_load(p, ttl_seconds=3600) == {"raw": "no wrapper"}
