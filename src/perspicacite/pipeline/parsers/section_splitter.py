"""ASB-aligned heuristic IMRaD section splitter.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Adapted: ``split_sections`` accepts a plain ``str`` instead of ASB's
``IngestResult``. Behavior otherwise identical (same alias map, same heading
regexes, same fallback semantics).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SECTION_ALIASES: dict[str, str] = {
    "abstract": "abstract",
    "introduction": "intro",
    "intro": "intro",
    "background": "intro",
    "methods": "methods",
    "method": "methods",
    "materials and methods": "methods",
    "experimental": "methods",
    "experimental section": "methods",
    "results": "results",
    "results and discussion": "results",
    "results & discussion": "results",
    "findings": "results",
    "discussion": "discussion",
    "conclusion": "discussion",
    "conclusions": "discussion",
    "limitations": "discussion",
    "supplementary": "supplementary",
    "supplementary material": "supplementary",
    "supporting information": "supplementary",
    "associated content": "supplementary",
    "appendix": "supplementary",
}

KNOWN_SECTIONS: tuple[str, ...] = (
    "abstract", "intro", "methods", "results", "discussion", "supplementary", "other",
)

_HEADING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s{0,3}#{1,3}\s*([A-Za-z][A-Za-z &]+?)\s*#*\s*$"),
    re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+([A-Za-z][A-Za-z &]+?)\s*$"),
    re.compile(r"^\s*[■▪●□◆◇▶▷]\s*([A-Za-z][A-Za-z &]+?)\s*$"),
    re.compile(r"^\s*([A-Z][A-Z &]{2,80})\s*$"),
    re.compile(r"^\s*([A-Z][A-Za-z &]{1,80})\s*$"),
)


@dataclass
class SectionMap:
    sections: dict[str, str] = field(default_factory=dict)
    sections_detected: bool = True


def split_sections(text: str) -> SectionMap:
    """Split ``text`` into IMRaD sections, or fall back to a ``full_text`` bucket.

    Adapted from ASB's ``split_sections(IngestResult)``; behavior identical.
    """
    full_text = text or ""
    if not full_text.strip():
        return SectionMap(sections={"full_text": ""}, sections_detected=False)

    lines = full_text.splitlines()
    buckets: dict[str, list[str]] = {}
    current: str | None = None
    detected_any = False
    in_table = False

    for line in lines:
        if in_table:
            if current is not None:
                buckets[current].append(line)
            if line.lstrip().startswith("<!--TABLE_END-->"):
                in_table = False
            continue
        if line.lstrip().startswith("<!--TABLE_BEGIN"):
            in_table = True
            if current is not None:
                buckets[current].append(line)
            continue
        canonical = _match_heading(line)
        if canonical is not None:
            current = canonical
            buckets.setdefault(current, [])
            detected_any = True
            continue
        if current is None:
            continue
        buckets[current].append(line)

    if not detected_any:
        return SectionMap(sections={"full_text": full_text.strip()}, sections_detected=False)

    sections = {
        name: "\n".join(content_lines).strip()
        for name, content_lines in buckets.items()
        if content_lines
    }
    return SectionMap(sections=sections, sections_detected=True)


def _match_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or len(stripped) > 120:
        return None
    for pattern in _HEADING_PATTERNS:
        match = pattern.match(line)
        if not match:
            continue
        candidate = match.group(1).strip().lower()
        if candidate in SECTION_ALIASES:
            return SECTION_ALIASES[candidate]
    return None
