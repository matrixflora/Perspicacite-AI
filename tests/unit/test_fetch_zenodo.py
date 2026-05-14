from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.pipeline.external import fetch_zenodo as fz_mod


def _meta(files):
    return {
        "id": 1234567,
        "metadata": {"title": "Test record"},
        "files": files,
    }


@pytest.mark.asyncio
async def test_metadata_only_writes_json_no_blob_fetch(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    meta = _meta([{"key": "data.csv", "size": 100, "links": {"self": "https://x/data.csv"}}])

    with patch.object(fz_mod, "http_get_json",
                      new=AsyncMock(return_value=meta)), \
         patch.object(fz_mod, "http_get_bytes",
                      new=AsyncMock(side_effect=AssertionError("should not fetch blobs"))):
        r = await fz_mod.fetch_zenodo(
            "1234567", capsule_dir=cap, cache_dir=tmp_path / "cache",
        )
    assert (cap / "external" / "zenodo" / "1234567.json").exists()
    assert r["files_fetched"] == 0
    assert r["metadata_path"]


@pytest.mark.asyncio
async def test_fetch_small_text_with_allowlist(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    meta = _meta([
        {"key": "README.md", "size": 200, "links": {"self": "https://x/README.md"}},
        {"key": "analysis.py", "size": 300, "links": {"self": "https://x/analysis.py"}},
        {"key": "data.csv", "size": 999999, "links": {"self": "https://x/data.csv"}},
    ])

    async def fake_bytes(url, **kw):
        if url.endswith("README.md"):
            return b"# title\nhello"
        if url.endswith("analysis.py"):
            return b"print('hi')\n"
        raise AssertionError(f"unexpected url: {url}")

    with patch.object(fz_mod, "http_get_json", new=AsyncMock(return_value=meta)), \
         patch.object(fz_mod, "http_get_bytes", new=AsyncMock(side_effect=fake_bytes)):
        r = await fz_mod.fetch_zenodo(
            "1234567", capsule_dir=cap, cache_dir=tmp_path / "cache",
            text_file_extensions=[".md", ".py"],
            max_bytes_per_file=10_000,
            metadata_only=False,
        )
    assert r["files_fetched"] == 2
    files_dir = cap / "external" / "zenodo" / "1234567" / "files"
    assert (files_dir / "README.md").exists()
    assert (files_dir / "analysis.py").exists()
    assert not (files_dir / "data.csv").exists()


@pytest.mark.asyncio
async def test_archive_extensions_always_skipped(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    meta = _meta([
        {"key": "code.tar.gz", "size": 100, "links": {"self": "https://x/code.tar.gz"}},
        {"key": "bundle.zip", "size": 100, "links": {"self": "https://x/bundle.zip"}},
    ])

    with patch.object(fz_mod, "http_get_json", new=AsyncMock(return_value=meta)), \
         patch.object(fz_mod, "http_get_bytes",
                      new=AsyncMock(side_effect=AssertionError("archives must not be fetched"))):
        r = await fz_mod.fetch_zenodo(
            "1234567", capsule_dir=cap, cache_dir=tmp_path / "cache",
            text_file_extensions=[".gz", ".zip"],
            metadata_only=False,
        )
    assert r["files_fetched"] == 0


@pytest.mark.asyncio
async def test_per_record_budget_enforced(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    meta = _meta([
        {"key": "a.md", "size": 400_000, "links": {"self": "https://x/a.md"}},
        {"key": "b.md", "size": 400_000, "links": {"self": "https://x/b.md"}},
        {"key": "c.md", "size": 400_000, "links": {"self": "https://x/c.md"}},
    ])

    async def fake_bytes(url, **kw):
        return b"x" * 400_000

    with patch.object(fz_mod, "http_get_json", new=AsyncMock(return_value=meta)), \
         patch.object(fz_mod, "http_get_bytes", new=AsyncMock(side_effect=fake_bytes)):
        r = await fz_mod.fetch_zenodo(
            "1234567", capsule_dir=cap, cache_dir=tmp_path / "cache",
            text_file_extensions=[".md"],
            max_bytes_per_file=500_000,
            max_bytes_per_record=700_000,
            metadata_only=False,
        )
    assert r["files_fetched"] == 1


@pytest.mark.asyncio
async def test_oversized_single_file_skipped(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    meta = _meta([
        {"key": "huge.json", "size": 2_000_000, "links": {"self": "https://x/huge.json"}},
    ])

    with patch.object(fz_mod, "http_get_json", new=AsyncMock(return_value=meta)), \
         patch.object(fz_mod, "http_get_bytes",
                      new=AsyncMock(side_effect=AssertionError("oversized must skip"))):
        r = await fz_mod.fetch_zenodo(
            "1234567", capsule_dir=cap, cache_dir=tmp_path / "cache",
            text_file_extensions=[".json"],
            max_bytes_per_file=500_000,
            metadata_only=False,
        )
    assert r["files_fetched"] == 0


@pytest.mark.asyncio
async def test_metadata_fetch_failure_returns_empty_summary(tmp_path):
    cap = tmp_path / "cap"
    cap.mkdir()
    with patch.object(fz_mod, "http_get_json", new=AsyncMock(return_value=None)):
        r = await fz_mod.fetch_zenodo("9999", capsule_dir=cap, cache_dir=tmp_path / "cache")
    assert r["files_fetched"] == 0
    assert r["metadata_path"] is None
