"""Candidate-pair pruning for CiTO classification.

We never feed every claim x claim pair to the LLM - that scales O(N^2) on a
50-paper / 400-claim KB. Instead we pre-filter to pairs whose subjects or
objects share a lemma, or that come from the same paper neighborhood, then
cap the per-claim fan-out at ``max_pairs_per_claim`` (default 20).

Lemmatization is intentionally cheap (lowercase + strip + split punctuation);
the CiTO classifier itself is the smart layer that decides whether two
lemma-overlapping claims actually relate.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _lemmas(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def _shares_lemma(a: dict, b: dict, slot: str) -> bool:
    return bool(_lemmas(a.get(slot, "")) & _lemmas(b.get(slot, "")))


def build_candidate_pairs(
    claims: list[dict[str, Any]],
    *,
    max_pairs_per_claim: int = 20,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Return candidate (claim_a, claim_b) pairs for CiTO classification.

    A pair is a candidate if any of:
      - they share a lemma in `subject`
      - they share a lemma in `object`
      - they come from the same `_paper_id` (only if multiple papers exist)
    """
    pairs: list[tuple[dict, dict]] = []
    per_claim_count: dict[int, int] = {}

    # Check if multiple papers exist
    papers = {c.get("_paper_id") for c in claims if c.get("_paper_id") is not None}
    use_paper_neighborhood = len(papers) > 1

    n = len(claims)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = claims[i], claims[j]
            same_paper = (
                use_paper_neighborhood
                and a.get("_paper_id") is not None
                and a.get("_paper_id") == b.get("_paper_id")
            )
            if not (_shares_lemma(a, b, "subject") or _shares_lemma(a, b, "object") or same_paper):
                continue
            if per_claim_count.get(i, 0) >= max_pairs_per_claim:
                continue
            if per_claim_count.get(j, 0) >= max_pairs_per_claim:
                continue
            pairs.append((a, b))
            per_claim_count[i] = per_claim_count.get(i, 0) + 1
            per_claim_count[j] = per_claim_count.get(j, 0) + 1
    return pairs
