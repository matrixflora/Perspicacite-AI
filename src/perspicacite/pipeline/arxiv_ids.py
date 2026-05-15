"""Parsing helpers for arXiv-style DOIs.

OpenAlex frequently indexes arXiv preprints with no DOI link even though the
canonical DOI of the form ``10.48550/arXiv.YYYY.NNNNN`` exists. We parse the
arXiv id out of that DOI and use the ``ids.arxiv`` OpenAlex filter as a
fallback when ``/works/doi:`` returns 404.
"""
from __future__ import annotations

import re
from typing import Optional

# Matches: 10.48550/arXiv.2005.11401  /  10.48550/arxiv.2305.12345v2
_ARXIV_DOI_RE = re.compile(
    r"^\s*10\.48550/arxiv\.(\d{4}\.\d{4,5}(?:v\d+)?)\s*$",
    re.IGNORECASE,
)


def parse_arxiv_doi(doi: Optional[str]) -> Optional[str]:
    """Return arXiv id (e.g. ``2005.11401``) or None if not an arXiv DOI."""
    if not doi:
        return None
    m = _ARXIV_DOI_RE.match(doi)
    return m.group(1) if m else None
