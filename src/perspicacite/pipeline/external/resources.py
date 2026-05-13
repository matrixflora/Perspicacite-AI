"""ASB-aligned resource-URL extraction (DOI / GitHub / Zenodo).

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Pure stdlib regex extraction. Network fetchers (Cycle C) live in
``pipeline/external/fetch.py``; they are not in this Cycle.
"""

from __future__ import annotations

import re

_DOI_PATTERN = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)
_GITHUB_PATTERN = re.compile(
    r"(?:https?://)?github\.com/([\w.-]+)/([\w][\w.-]*)",
    re.IGNORECASE,
)
_ZENODO_URL_PATTERN = re.compile(
    r"https?://(?:www\.)?zenodo\.org/records?/(\d+)", re.IGNORECASE
)
_ZENODO_DOI_PATTERN = re.compile(r"10\.5281/zenodo\.(\d+)", re.IGNORECASE)


def extract_doi_candidates(text: str) -> list[str]:
    """Return deduplicated DOI candidates discovered in the corpus."""

    seen: list[str] = []
    for match in _DOI_PATTERN.finditer(text):
        candidate = match.group(0).rstrip(").,;:")
        # Crossref DOIs are case-insensitive but conventionally lowercased on
        # the prefix; preserve as-found and dedupe by lowercase.
        if candidate.lower() in {existing.lower() for existing in seen}:
            continue
        # Filter out Zenodo DOIs from the article-level DOI list — they belong
        # to dataset deposits and are queried separately.
        if _ZENODO_DOI_PATTERN.fullmatch(candidate):
            continue
        seen.append(candidate)
    return seen


def extract_github_repos(text: str) -> list[str]:
    """Return deduplicated ``owner/repo`` strings discovered in the corpus."""

    repos: list[str] = []
    for match in _GITHUB_PATTERN.finditer(text):
        owner, repo = match.group(1), match.group(2)
        # Trim trailing punctuation (".", ",", ";") and ".git" suffix.
        repo = repo.rstrip(".,;:)/")
        if repo.endswith(".git"):
            repo = repo[:-4]
        if not repo:
            continue
        if owner.lower() in {"orgs", "search", "topics", "about", "marketplace"}:
            continue
        full = f"{owner}/{repo}"
        if full not in repos:
            repos.append(full)
    return repos


def extract_zenodo_record_ids(text: str) -> list[str]:
    """Return deduplicated Zenodo record IDs from URLs and DOIs in the corpus."""

    ids: list[str] = []
    for pattern in (_ZENODO_URL_PATTERN, _ZENODO_DOI_PATTERN):
        for match in pattern.finditer(text):
            record_id = match.group(1)
            if record_id not in ids:
                ids.append(record_id)
    return ids
