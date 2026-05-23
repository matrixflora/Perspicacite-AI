"""GitHub-repo / skill-bundle ingest pipeline (2026-05-15 spec).

See ``docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md``
for the design and ``docs/superpowers/plans/2026-05-15-github-skill-bundle-ingest.md``
for the rollout plan.

The fetcher's public symbols are re-exported here so callers can write::

    from perspicacite.pipeline.github import GitHubFetcher, parse_repo_url

instead of digging into the ``.fetcher`` submodule.
"""

from perspicacite.pipeline.github.bundle import (
    DEFAULT_EXCLUDE_GLOBS,
    DEFAULT_INCLUDE_GLOBS,
    BundleManifest,
    ContentSpec,
    LinkBag,
    PaperRef,
    extract_links_from_text,
)
from perspicacite.pipeline.github.chunk_producer import papers_from_directory
from perspicacite.pipeline.github.fetcher import (
    FetcherError,
    GitHubFetcher,
    RateLimitedError,
    RepoRef,
    parse_repo_url,
)
from perspicacite.pipeline.github.walk import walk_filtered

__all__ = [
    "DEFAULT_EXCLUDE_GLOBS",
    "DEFAULT_INCLUDE_GLOBS",
    "BundleManifest",
    "ContentSpec",
    "FetcherError",
    "GitHubFetcher",
    "LinkBag",
    "PaperRef",
    "RateLimitedError",
    "RepoRef",
    "extract_links_from_text",
    "papers_from_directory",
    "parse_repo_url",
    "walk_filtered",
]
