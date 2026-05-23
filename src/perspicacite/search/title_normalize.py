"""Cheap title normalisation for search-retry on 0-result queries.

Drops everything after the first colon ("Title: subtitle" → "Title")
and removes parentheticals. Cheap heuristic, not LLM-driven — fast
retry alternative to --rephrase."""
from __future__ import annotations

import re

_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def normalize_title(query: str) -> str:
    """Return a stripped form suitable for a 0-result retry.

    - Removes parentheticals: ``"X (v2)"`` → ``"X"``
    - Strips everything after the first colon: ``"X: Y"`` → ``"X"``

    Falls back to the original query when the normalisation would
    produce an empty string.
    """
    s = _PAREN_RE.sub("", query).strip()
    if ":" in s:
        s = s.split(":", 1)[0].strip()
    return s or query


def is_titlelike(query: str) -> bool:
    """A query that has subtitle/parenthetical structure is a candidate
    for normalize-retry on zero results."""
    return ":" in query or "(" in query
