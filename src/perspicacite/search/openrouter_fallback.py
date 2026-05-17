"""OpenRouter web-search fallback for Google Scholar CAPTCHA events.

Called by GoogleScholarPlaywrightProvider when Scholar returns a CAPTCHA.
Uses OpenRouter's ``openrouter:web_search`` server tool (Exa engine) with an
academic domain allowlist to retrieve paper metadata via an LLM.

No external dependencies beyond httpx (already in project requirements).
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.models.papers import Author, Paper, PaperSource

logger = get_logger("perspicacite.search.openrouter_fallback")

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_DEFAULT_DOMAINS: list[str] = [
    "arxiv.org",
    "biorxiv.org",
    "chemrxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "europepmc.org",
    "semanticscholar.org",
    "crossref.org",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "wiley.com",
]

_PROMPT_TEMPLATE = (
    "Search scientific literature for papers about: {query}\n"
    "Return ONLY a JSON array of up to {n} papers. Each element must be:\n"
    '  {{"title": str, "authors": [str], "year": int or null, '
    '"doi": str or null, "abstract": str, "url": str}}\n'
    "No prose, no markdown, no explanation. Just the raw JSON array."
)


def _resolve_api_key(config_key: str) -> str:
    """Return config key if non-empty, else OPENROUTER_API_KEY env var, else ''."""
    if config_key:
        return config_key
    return os.environ.get("OPENROUTER_API_KEY", "")


def _build_payload(
    query: str,
    model: str,
    max_results: int,
    allowed_domains: list[str],
) -> dict[str, Any]:
    return {
        "model": model,
        "tool_choice": "required",
        "tools": [
            {
                "type": "openrouter:web_search",
                "parameters": {
                    "engine": "exa",
                    "max_results": min(max_results, 25),
                    "allowed_domains": allowed_domains,
                },
            }
        ],
        "messages": [
            {
                "role": "user",
                "content": _PROMPT_TEMPLATE.format(query=query, n=max_results),
            }
        ],
    }


def _parse_response(content: str) -> list[dict[str, Any]]:
    """Extract and parse the outermost JSON array from LLM response text."""
    start = content.find("[")
    end = content.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        result = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(result, list):
        return []
    return result


def _build_paper(entry: dict[str, Any]) -> Paper | None:
    """Convert a parsed JSON entry to a Paper. Returns None on unrecoverable errors."""
    try:
        title = str(entry.get("title") or "Untitled").strip()
        doi = entry.get("doi") or None
        url = str(entry.get("url") or "")
        year_raw = entry.get("year")
        year: int | None = int(year_raw) if year_raw is not None else None

        authors: list[Author] = []
        for name in entry.get("authors") or []:
            name_str = str(name).strip()
            if name_str:
                authors.append(Author(name=name_str))

        abstract = str(entry.get("abstract") or "").strip() or None
        _hash = hashlib.sha256((url or title or "unknown").encode()).hexdigest()[:8]
        paper_id = doi or f"openrouter:{_hash}"

        return Paper(
            id=paper_id,
            title=title,
            authors=authors,
            year=year,
            doi=doi,
            abstract=abstract,
            source=PaperSource.OPENROUTER_WEB,
            metadata={"sources": ["openrouter_web"], "scholar_url": url},
        )
    except Exception as exc:
        logger.warning("openrouter_fallback_build_paper_error", error=str(exc))
        return None


async def openrouter_academic_search(
    query: str,
    *,
    api_key: str,
    model: str = "deepseek/deepseek-chat",
    max_results: int = 10,
    allowed_domains: list[str] | None = None,
    timeout: float = 20.0,
) -> list[Paper]:
    """Call OpenRouter web_search server tool and return Paper objects.

    Returns [] on any error (HTTP failure, bad API key, parse failure).
    Never raises.
    """
    key = _resolve_api_key(api_key)
    if not key:
        logger.warning("openrouter_fallback_no_key")
        return []

    domains = allowed_domains if allowed_domains is not None else _DEFAULT_DOMAINS
    payload = _build_payload(query, model, max_results, domains)

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("openrouter_fallback_http_error", error=str(exc))
            return []

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"] or ""
        entries = _parse_response(content)
    except Exception as exc:
        logger.warning("openrouter_fallback_parse_error", error=str(exc))
        return []

    papers: list[Paper] = []
    for entry in entries:
        paper = _build_paper(entry)
        if paper:
            papers.append(paper)

    logger.info("openrouter_fallback_done", query=query[:80], n=len(papers))
    return papers
