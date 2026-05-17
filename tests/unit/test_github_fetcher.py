"""Tests for GitHub fetcher — parse_repo_url and GitHubFetcher (mock-driven)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.pipeline.github.fetcher import (
    GitHubFetcher,
    RepoRef,
    parse_repo_url,
)

# ── parse_repo_url ────────────────────────────────────────────────────────────

def test_basic_url():
    r = parse_repo_url("https://github.com/org/repo")
    assert r == RepoRef(org="org", repo="repo", ref=None, subpath=None)


def test_with_branch_via_at():
    r = parse_repo_url("https://github.com/org/repo@main")
    assert r.ref == "main"


def test_with_commit_sha_via_at():
    r = parse_repo_url("https://github.com/org/repo@abc1234")
    assert r.ref == "abc1234"


def test_with_tree_path():
    r = parse_repo_url("https://github.com/org/repo/tree/main/bundles/scrna-qc")
    assert r.ref == "main"
    assert r.subpath == "bundles/scrna-qc"


def test_blob_url_raises():
    with pytest.raises(ValueError, match="blob"):
        parse_repo_url("https://github.com/org/repo/blob/main/README.md")


def test_tree_url_without_subpath():
    r = parse_repo_url("https://github.com/org/repo/tree/develop")
    assert r.ref == "develop"
    assert r.subpath is None


# ── GitHubFetcher ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_resolve_commit_sha_calls_api(tmp_path):
    fetcher = GitHubFetcher(token="tok", cache_dir=tmp_path)
    ref = RepoRef(org="deepmind", repo="alphafold")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"sha": "abc123def456"}
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        sha = await fetcher.resolve_commit_sha(ref)

    assert sha == "abc123def456"
    call_url = mock_client.get.call_args[0][0]
    assert "deepmind/alphafold/commits" in call_url


@pytest.mark.asyncio
async def test_fetch_tarball_uses_cache_on_second_call(tmp_path):
    sha = "deadbeef1234"
    cached_dir = tmp_path / sha
    cached_dir.mkdir()
    fetcher = GitHubFetcher(cache_dir=tmp_path)
    ref = RepoRef(org="org", repo="repo")

    # Should return the cached dir without any HTTP call
    with patch("httpx.AsyncClient") as mock_client_cls:
        result = await fetcher.fetch_tarball(ref, sha=sha)

    assert result == cached_dir
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
async def test_auth_header_sent_when_token_provided(tmp_path):
    fetcher = GitHubFetcher(token="ghp_test_token", cache_dir=tmp_path)
    headers = fetcher._headers()
    assert headers["Authorization"] == "Bearer ghp_test_token"


def test_no_auth_header_without_token(tmp_path):
    fetcher = GitHubFetcher(cache_dir=tmp_path)
    headers = fetcher._headers()
    assert "Authorization" not in headers
