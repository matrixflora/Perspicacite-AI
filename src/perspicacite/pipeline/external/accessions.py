"""ASB-aligned regex mining of public data-repository accession IDs.

Synced from AgenticScienceBuilder @ 809f478 — keep API in sync.

Pure stdlib, network-free. Each match is converted into a structured record
with a navigable URL and a short evidence snippet so capsules can reference
deposited data without depending on any LLM/network pass.
"""

from __future__ import annotations

import re

# (kind, compiled_regex, url_template). url_template uses ``{id}`` placeholder.
_ACCESSION_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("massive",      re.compile(r"\bMSV\d{9}\b"),
        "https://massive.ucsd.edu/ProteoSAFe/dataset.jsp?accession={id}"),
    ("pride",        re.compile(r"\bPXD\d{6,}\b"),
        "https://www.ebi.ac.uk/pride/archive/projects/{id}"),
    ("metabolights", re.compile(r"\bMTBLS\d+\b"),
        "https://www.ebi.ac.uk/metabolights/{id}"),
    ("geo_series",   re.compile(r"\bGSE\d+\b"),
        "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={id}"),
    ("bioproject",   re.compile(r"\bPRJ(?:NA|EB|DB)\d+\b"),
        "https://www.ncbi.nlm.nih.gov/bioproject/{id}"),
    ("sra_run",      re.compile(r"\b[SED]RR\d{5,}\b"),
        "https://www.ncbi.nlm.nih.gov/sra/?term={id}"),
)


_SNIPPET_RADIUS = 60  # chars on each side of the match for evidence_span


def mine_accessions(text: str) -> list[dict[str, str]]:
    """Find data-repository accessions in ``text`` and return structured records.

    Each record is ``{"kind", "accession", "url", "evidence_span"}``. Records
    are deduped on ``(kind, accession)`` keeping the first occurrence. Order
    is stable: by kind precedence (the order in ``_ACCESSION_PATTERNS``), then
    by first match offset within the text.
    """
    if not text:
        return []
    seen: set[tuple[str, str]] = set()
    records: list[dict[str, str]] = []
    for kind, pattern, url_template in _ACCESSION_PATTERNS:
        for match in pattern.finditer(text):
            acc = match.group(0)
            key = (kind, acc)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, match.start() - _SNIPPET_RADIUS)
            end = min(len(text), match.end() + _SNIPPET_RADIUS)
            snippet = text[start:end].replace("\n", " ").strip()
            records.append({
                "kind": kind,
                "accession": acc,
                "url": url_template.format(id=acc),
                "evidence_span": snippet,
            })
    return records
