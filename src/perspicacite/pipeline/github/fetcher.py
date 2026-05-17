"""GitHub repository fetcher with tarball download + SHA-based caching.

Usage::

    fetcher = GitHubFetcher(token="ghp_...", cache_dir=Path("data/github_cache"))
    root, sha = await fetcher.fetch(RepoRef(org="deepmind", repo="alphafold"))
"""
from __future__ import annotations

import asyncio
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.github.fetcher")


@dataclass
class RepoRef:
    org: str
    repo: str
    ref: str | None = None
    subpath: str | None = None


def parse_repo_url(url: str) -> RepoRef:
    """Parse a GitHub URL into a RepoRef.

    Handles:
    - https://github.com/org/repo
    - https://github.com/org/repo@ref
    - https://github.com/org/repo/tree/ref/subpath

    Raises ValueError for blob URLs (not directory targets).
    """
    # blob URLs are not directory targets
    if "/blob/" in url:
        raise ValueError(f"blob URL is not a directory target: {url}")

    # @ref suffix
    at_match = re.match(r"https://github\.com/([^/]+)/([^/@]+)@([^/\s]+)$", url)
    if at_match:
        return RepoRef(org=at_match.group(1), repo=at_match.group(2), ref=at_match.group(3))

    # /tree/ref/subpath
    tree_match = re.match(r"https://github\.com/([^/]+)/([^/]+)/tree/([^/]+)(?:/(.+))?$", url)
    if tree_match:
        return RepoRef(
            org=tree_match.group(1),
            repo=tree_match.group(2),
            ref=tree_match.group(3),
            subpath=tree_match.group(4),
        )

    # basic: https://github.com/org/repo
    basic_match = re.match(r"https://github\.com/([^/]+)/([^/]+)/?$", url)
    if basic_match:
        return RepoRef(org=basic_match.group(1), repo=basic_match.group(2))

    raise ValueError(f"Cannot parse GitHub URL: {url!r}")


class GitHubFetcher:
    def __init__(
        self,
        *,
        token: str | None = None,
        cache_dir: Path,
        user_agent: str = "Perspicacite/2.0",
        api_base: str = "https://api.github.com",
    ) -> None:
        self._token = token
        self._cache_dir = Path(cache_dir)
        self._user_agent = user_agent
        self._api_base = api_base.rstrip("/")

    def _headers(self) -> dict[str, str]:
        h = {"User-Agent": self._user_agent, "Accept": "application/vnd.github+json"}
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def resolve_commit_sha(self, ref: RepoRef) -> str:
        """GET /repos/{org}/{repo}/commits/{ref} → sha."""
        branch = ref.ref or "HEAD"
        url = f"{self._api_base}/repos/{ref.org}/{ref.repo}/commits/{branch}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), follow_redirects=True)
            resp.raise_for_status()
            return resp.json()["sha"]

    async def fetch_tarball(self, ref: RepoRef, *, sha: str) -> Path:
        """Download and extract the tarball for the given SHA.

        SHA cache hit returns the cached path without re-downloading.
        """
        dest = self._cache_dir / sha
        if dest.exists():
            logger.info("github_tarball_cache_hit", sha=sha[:8])
            return dest

        dest.mkdir(parents=True, exist_ok=True)
        url = f"https://github.com/{ref.org}/{ref.repo}/archive/{sha}.tar.gz"
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(url, headers=self._headers())
            resp.raise_for_status()

        # Extract
        import io
        with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:gz") as tar:
            tar.extractall(dest, filter="data")

        return dest

    async def fetch_clone(self, ref: RepoRef, *, sha: str) -> Path:
        """Shallow git clone fallback when tarball is rate-limited."""
        dest = self._cache_dir / sha
        if dest.exists():
            return dest
        dest.mkdir(parents=True, exist_ok=True)
        repo_url = f"https://github.com/{ref.org}/{ref.repo}.git"
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", repo_url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()
        return dest

    async def fetch(self, ref: RepoRef) -> tuple[Path, str]:
        """High-level: resolve SHA, hit cache, try tarball, fall back to clone.

        Returns (root_path, sha).
        """
        sha = await self.resolve_commit_sha(ref)
        try:
            path = await self.fetch_tarball(ref, sha=sha)
        except Exception as exc:
            logger.warning("github_tarball_failed_falling_back_to_clone", error=str(exc))
            path = await self.fetch_clone(ref, sha=sha)
        return path, sha
