"""Deterministic topic heuristic for recommending literature databases.

No LLM is involved: a query (plus optional hints) is matched against a small
keyword rule list, and the union of matched databases is merged with a broad
default set so a recommendation is always returned. Only databases present in
``KNOWN_DATABASES`` are surfaced.
"""
from __future__ import annotations

from dataclasses import dataclass

from perspicacite.search.scilex_adapter import KNOWN_DATABASES

# Broad, always-included fallback. These cover most disciplines and are safe
# defaults when no topic-specific signal is detected.
_DEFAULT_DATABASES: list[str] = ["semantic_scholar", "openalex", "crossref"]

# Ordered keyword → databases rules. The first databases listed for matched
# topics are prepended to the broad default, giving topic-relevant sources
# priority while preserving the general fallback.
_RULES: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
    (
        "biomedical/life sciences",
        (
            "crispr",
            "gene",
            "genom",
            "protein",
            "cell",
            "clinical",
            "disease",
            "cancer",
            "biomedical",
            "medic",
            "drug",
            "therapy",
            "rna",
            "dna",
            "neuro",
            "immun",
            "microbiome",
            "pathogen",
        ),
        ("pubmed", "europepmc"),
    ),
    (
        "computer science / machine learning",
        (
            "machine learning",
            "deep learning",
            "neural network",
            "transformer",
            "language model",
            "algorithm",
            "computer vision",
            "reinforcement learning",
            "embedding",
            "llm",
        ),
        ("arxiv",),
    ),
    (
        "physics",
        ("physics", "quantum", "relativity", "condensed matter", "astrophys"),
        ("arxiv",),
    ),
    (
        "high-energy physics",
        (
            "high-energy",
            "high energy",
            "particle",
            "collider",
            "quark",
            "gluon",
            "boson",
            "hadron",
        ),
        ("inspire",),
    ),
    (
        "chemistry",
        (
            "chemistry",
            "chemical",
            "molecule",
            "compound",
            "synthesis",
            "reaction",
            "catalyst",
            "organic",
        ),
        ("pubchem",),
    ),
]


@dataclass(frozen=True)
class DatabaseSuggestion:
    """Result of a database recommendation."""

    databases: list[str]
    reasoning: str


def suggest_databases_for_query(
    query: str, hints: list[str] | None = None
) -> DatabaseSuggestion:
    """Recommend which literature databases to search for ``query``.

    Deterministic: a lowercased match of the query (and optional ``hints``)
    against a keyword rule list selects topic-specific databases, which are
    merged with a broad default set. Only databases in ``KNOWN_DATABASES`` are
    returned; the list is deduped preserving order and is never empty.
    """
    haystack = " ".join([query or "", *(hints or [])]).lower()

    ordered: list[str] = []
    matched_topics: list[str] = []
    for topic, keywords, databases in _RULES:
        if any(kw in haystack for kw in keywords):
            matched_topics.append(topic)
            ordered.extend(databases)

    ordered.extend(_DEFAULT_DATABASES)

    # Dedupe preserving order, dropping anything not in KNOWN_DATABASES.
    seen: set[str] = set()
    result: list[str] = []
    for db in ordered:
        if db in KNOWN_DATABASES and db not in seen:
            seen.add(db)
            result.append(db)

    if matched_topics:
        reasoning = (
            "Matched topic(s): "
            + ", ".join(matched_topics)
            + ". Recommending topic-specific databases alongside broad defaults."
        )
    else:
        reasoning = (
            "No specific topic detected; recommending broad cross-disciplinary "
            "databases."
        )

    return DatabaseSuggestion(databases=result, reasoning=reasoning)
