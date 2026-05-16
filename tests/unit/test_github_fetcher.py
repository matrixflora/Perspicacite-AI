"""Tests for ``perspicacite.pipeline.github.fetcher``.

Covers URL parsing, ``GitHubFetcher`` HTTP path (tarball download +
on-disk SHA cache + token + rate-limit), and the ``git clone`` fallback
when the tarball endpoint is rate-limited.

The fetcher is the foundation of the 2026-05-15 GitHub / skill-bundle
ingest design — see
``docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md``
for the wider picture.
"""

from __future__ import annotations

import io
import tarfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from perspicacite.pipeline.github.fetcher import (
    FetcherError,
    GitHubFetcher,
    RateLimitedError,
    RepoRef,
    parse_repo_url,
)

# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------


class TestParseRepoUrl:
    """Lossless decomposition of GitHub repository URLs into ``RepoRef``."""

    def test_basic_url(self) -> None:
        assert parse_repo_url("https://github.com/org/repo") == RepoRef(
            org="org", repo="repo", ref=None, subpath=None
        )

    def test_with_branch_via_at(self) -> None:
        r = parse_repo_url("https://github.com/org/repo@main")
        assert r == RepoRef(org="org", repo="repo", ref="main", subpath=None)

    def test_with_commit_sha_via_at(self) -> None:
        r = parse_repo_url("https://github.com/org/repo@abc1234")
        assert r.ref == "abc1234"
        assert r.subpath is None

    def test_with_tree_path(self) -> None:
        r = parse_repo_url(
            "https://github.com/org/repo/tree/main/bundles/scrna-qc"
        )
        assert r == RepoRef(
            org="org", repo="repo", ref="main", subpath="bundles/scrna-qc"
        )

    def test_tree_branch_only_no_subpath(self) -> None:
        r = parse_repo_url("https://github.com/org/repo/tree/dev")
        assert r == RepoRef(org="org", repo="repo", ref="dev", subpath=None)

    def test_blob_url_rejected(self) -> None:
        """``/blob/<branch>/<file>`` targets a file, not a directory; the
        skill-bundle pipeline only ingests directories."""
        with pytest.raises(ValueError, match="blob"):
            parse_repo_url("https://github.com/org/repo/blob/main/README.md")

    def test_malformed_no_github_host(self) -> None:
        with pytest.raises(ValueError, match=r"github\.com"):
            parse_repo_url("https://example.com/org/repo")

    def test_trailing_slash_stripped(self) -> None:
        r = parse_repo_url("https://github.com/org/repo/")
        assert r == RepoRef(org="org", repo="repo", ref=None, subpath=None)

    def test_dot_git_suffix_stripped(self) -> None:
        r = parse_repo_url("https://github.com/org/repo.git")
        assert r == RepoRef(org="org", repo="repo", ref=None, subpath=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_tarball(files: dict[str, bytes], top_dir: str = "org-repo-abc1234") -> bytes:
    """Build a small in-memory tarball that mimics GitHub's
    ``codeload`` shape: a single top-level directory containing files."""

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in files.items():
            info = tarfile.TarInfo(name=f"{top_dir}/{name}")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GitHubFetcher.resolve_commit_sha
# ---------------------------------------------------------------------------


async def test_resolve_commit_sha_returns_sha(
    tmp_path: Path, respx_mock: Any
) -> None:
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/main"
    ).mock(return_value=httpx.Response(200, json={"sha": "abc1234deadbeef"}))

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")
    sha = await fetcher.resolve_commit_sha(
        RepoRef(org="org", repo="repo", ref="main", subpath=None)
    )
    assert sha == "abc1234deadbeef"


async def test_resolve_commit_sha_uses_head_when_ref_none(
    tmp_path: Path, respx_mock: Any
) -> None:
    """``ref=None`` resolves against the literal ``HEAD`` so the GitHub
    API picks the repo's default branch (works for forks renamed
    main→default etc.)."""

    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/HEAD"
    ).mock(return_value=httpx.Response(200, json={"sha": "feedface"}))

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")
    sha = await fetcher.resolve_commit_sha(
        RepoRef(org="org", repo="repo", ref=None, subpath=None)
    )
    assert sha == "feedface"


async def test_resolve_commit_sha_404_raises_fetcher_error(
    tmp_path: Path, respx_mock: Any
) -> None:
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/missing/repo/commits/.*"
    ).mock(return_value=httpx.Response(404, json={"message": "Not Found"}))

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")
    with pytest.raises(FetcherError):
        await fetcher.resolve_commit_sha(
            RepoRef(org="missing", repo="repo", ref=None, subpath=None)
        )


# ---------------------------------------------------------------------------
# GitHubFetcher token + rate-limit headers
# ---------------------------------------------------------------------------


async def test_token_passed_as_authorization_header(
    tmp_path: Path, respx_mock: Any
) -> None:
    route = respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/main"
    ).mock(return_value=httpx.Response(200, json={"sha": "abc"}))

    fetcher = GitHubFetcher(token="ghp_secret", cache_dir=tmp_path / "cache")
    await fetcher.resolve_commit_sha(
        RepoRef(org="org", repo="repo", ref="main", subpath=None)
    )
    sent = route.calls[0].request
    assert sent.headers["authorization"] == "Bearer ghp_secret"
    # Always-on hygiene headers:
    assert "accept" in {h.lower() for h in sent.headers}
    assert sent.headers["accept"] == "application/vnd.github+json"
    assert sent.headers["user-agent"].startswith("Perspicacite")


async def test_rate_limit_raises_with_reset_at(
    tmp_path: Path, respx_mock: Any
) -> None:
    future = int(time.time()) + 3600
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/main"
    ).mock(
        return_value=httpx.Response(
            403,
            headers={"X-RateLimit-Reset": str(future)},
            json={"message": "API rate limit exceeded"},
        )
    )

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")
    with pytest.raises(RateLimitedError) as exc:
        await fetcher.resolve_commit_sha(
            RepoRef(org="org", repo="repo", ref="main", subpath=None)
        )
    assert exc.value.reset_at == future


# ---------------------------------------------------------------------------
# fetch_tarball — extraction + on-disk SHA cache
# ---------------------------------------------------------------------------


async def test_fetch_tarball_extracts_and_caches(
    tmp_path: Path, respx_mock: Any
) -> None:
    sha = "abc1234"
    tar_bytes = _build_tarball(
        {"README.md": b"hello\n", "src/x.py": b"print(1)\n"},
        top_dir=f"org-repo-{sha}",
    )
    route = respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/tarball/abc1234"
    ).mock(return_value=httpx.Response(200, content=tar_bytes))

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")
    ref = RepoRef(org="org", repo="repo", ref="main", subpath=None)

    path1 = await fetcher.fetch_tarball(ref, sha=sha)
    assert path1.is_dir()
    assert (path1 / "README.md").read_bytes() == b"hello\n"
    assert (path1 / "src" / "x.py").read_bytes() == b"print(1)\n"
    # Cache hit: second call must not hit the network.
    before = route.call_count
    path2 = await fetcher.fetch_tarball(ref, sha=sha)
    assert path2 == path1
    assert route.call_count == before, "second call should be a cache hit"


async def test_fetch_tarball_empty_cache_dir_is_refetched(
    tmp_path: Path, respx_mock: Any
) -> None:
    """An empty ``cache_dir/<sha>/`` (left by a previously-aborted
    extraction) is not a valid cache entry; the fetcher must clear it
    and re-download."""

    sha = "cafef00d"
    cache_root = tmp_path / "cache"
    (cache_root / sha).mkdir(parents=True)  # empty: stale partial extract

    tar_bytes = _build_tarball(
        {"README.md": b"ok\n"}, top_dir=f"org-repo-{sha}"
    )
    respx_mock.get(
        url__regex=rf"https://api\.github\.com/repos/org/repo/tarball/{sha}"
    ).mock(return_value=httpx.Response(200, content=tar_bytes))

    fetcher = GitHubFetcher(cache_dir=cache_root)
    path = await fetcher.fetch_tarball(
        RepoRef(org="org", repo="repo", ref="main", subpath=None), sha=sha
    )
    assert (path / "README.md").read_bytes() == b"ok\n"


# ---------------------------------------------------------------------------
# fetch_clone — git fallback
# ---------------------------------------------------------------------------


async def test_fetch_clone_invokes_git(tmp_path: Path) -> None:
    sha = "sha123"
    cache_root = tmp_path / "cache"
    fetcher = GitHubFetcher(cache_dir=cache_root)

    captured: dict[str, Any] = {}

    class _FakeProc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_exec(*args: str, **kwargs: Any) -> _FakeProc:
        captured.setdefault("calls", []).append(args)
        # Materialise the target directory so callers see it exist.
        # Last positional arg of ``git clone`` is the target path.
        if args[:2] == ("git", "clone"):
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
            (Path(args[-1]) / "README.md").write_text("hello")
        return _FakeProc()

    with patch(
        "perspicacite.pipeline.github.fetcher.asyncio.create_subprocess_exec",
        side_effect=_fake_exec,
    ):
        path = await fetcher.fetch_clone(
            RepoRef(org="org", repo="repo", ref="main", subpath=None), sha=sha
        )

    assert path == cache_root / sha
    assert (path / "README.md").read_text() == "hello"
    clone_argv = captured["calls"][0]
    assert clone_argv[:2] == ("git", "clone")
    assert "--depth=1" in clone_argv
    assert "https://github.com/org/repo.git" in clone_argv
    assert str(cache_root / sha) in clone_argv


# ---------------------------------------------------------------------------
# fetch() — orchestrator: tarball → clone fallback on rate-limit
# ---------------------------------------------------------------------------


async def test_fetch_falls_back_to_clone_on_rate_limit(
    tmp_path: Path, respx_mock: Any
) -> None:
    sha = "abcdef0"
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/main"
    ).mock(return_value=httpx.Response(200, json={"sha": sha}))

    fetcher = GitHubFetcher(cache_dir=tmp_path / "cache")

    async def _raise_rl(*args: Any, **kwargs: Any) -> Path:
        raise RateLimitedError("tarball over quota", reset_at=int(time.time()) + 60)

    async def _fake_clone(*args: Any, **kwargs: Any) -> Path:
        p = tmp_path / "cache" / sha
        p.mkdir(parents=True, exist_ok=True)
        (p / "README.md").write_text("cloned")
        return p

    with (
        patch.object(fetcher, "fetch_tarball", side_effect=_raise_rl),
        patch.object(fetcher, "fetch_clone", side_effect=_fake_clone) as mock_clone,
    ):
        path, returned_sha = await fetcher.fetch(
            RepoRef(org="org", repo="repo", ref="main", subpath=None)
        )

    assert returned_sha == sha
    assert path == tmp_path / "cache" / sha
    assert (path / "README.md").read_text() == "cloned"
    mock_clone.assert_called_once()


async def test_fetch_returns_cache_hit_without_clone(
    tmp_path: Path, respx_mock: Any
) -> None:
    sha = "cached1"
    respx_mock.get(
        url__regex=r"https://api\.github\.com/repos/org/repo/commits/main"
    ).mock(return_value=httpx.Response(200, json={"sha": sha}))

    cache_dir = tmp_path / "cache"
    (cache_dir / sha).mkdir(parents=True)
    (cache_dir / sha / "README.md").write_text("cached")

    fetcher = GitHubFetcher(cache_dir=cache_dir)
    clone_mock = AsyncMock()
    tarball_mock = AsyncMock()
    with (
        patch.object(fetcher, "fetch_clone", clone_mock),
        patch.object(fetcher, "fetch_tarball", tarball_mock),
    ):
        path, returned_sha = await fetcher.fetch(
            RepoRef(org="org", repo="repo", ref="main", subpath=None)
        )

    assert returned_sha == sha
    assert path == cache_dir / sha
    assert (path / "README.md").read_text() == "cached"
    clone_mock.assert_not_called()
    tarball_mock.assert_not_called()
