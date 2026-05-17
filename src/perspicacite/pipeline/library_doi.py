"""Library → canonical-paper DOI resolver for cite-graph enrichment.

Tries (in order):
1. A curated config map (KnowledgeBaseConfig.library_paper_map).
2. A bundle.yml `tools` entry with a `paper_doi` field.
3. README text scraping (regex patterns matching "Please cite [DOI]",
   "Citation: DOI", and the CITATION.cff `doi:` field).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

_DOI_RE = r"(10\.\d{4,9}/[\w./()\-:]+)"
# Relaxed DOI pattern for CITATION.cff `doi:` field — allows short registrant codes
# like 10.0/cff used in tests and some preprint servers.
_DOI_RE_RELAXED = r"(10\.\d+/[\w./()\-:]+)"

PATTERNS = [
    re.compile(
        rf"if you use\s+\S+\s+(?:in your|please).{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"please cite.{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        rf"citation\s*[:=].{{0,200}}?{_DOI_RE}",
        re.IGNORECASE | re.DOTALL,
    ),
    # CITATION.cff `doi:` field — relaxed pattern to accept short registrant codes
    re.compile(
        rf"^doi\s*:\s*{_DOI_RE_RELAXED}",
        re.IGNORECASE | re.MULTILINE,
    ),
]


@dataclass(frozen=True)
class LibraryPaper:
    library: str
    doi: str
    title: str | None
    source: Literal["config", "bundle", "readme"]
    confidence: float


async def resolve_library_paper(
    library: str,
    *,
    bundle: dict | None = None,
    github_repo: str | None = None,
    config_map: dict[str, str] | None = None,
    readme_text: str | None = None,
) -> LibraryPaper | None:
    """Resolve a library name to its canonical paper.

    Returns None when no source yields a DOI.
    """
    # 1. config map
    if config_map and library in config_map:
        return LibraryPaper(
            library=library,
            doi=config_map[library],
            title=None,
            source="config",
            confidence=1.0,
        )

    # 2. bundle.yml `tools[].paper_doi`
    if bundle:
        tools = bundle.get("tools") or []
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != library:
                continue
            doi = entry.get("paper_doi")
            if doi:
                return LibraryPaper(
                    library=library,
                    doi=doi,
                    title=entry.get("paper_title"),
                    source="bundle",
                    confidence=1.0,
                )

    # 3. README scrape
    if readme_text:
        for pat in PATTERNS:
            m = pat.search(readme_text)
            if m:
                return LibraryPaper(
                    library=library,
                    doi=m.group(1),
                    title=None,
                    source="readme",
                    confidence=0.6,
                )

    return None
