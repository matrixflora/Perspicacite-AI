"""Live smoke test for SS-fallback cite-graph on the RAG arXiv paper.

Pins the audit P3 finding: OpenAlex returns ~18 forward citations for
10.48550/arXiv.2005.11401; Semantic Scholar returns far more. The combined
snowball (include_semantic_scholar=True) should return at least 2x the
OpenAlex-only count, or at least 30 hits absolute (whichever is smaller —
accommodates rate-limited runs where SS is temporarily throttled).

Skipped cleanly without SEMANTIC_SCHOLAR_API_KEY or
SCILEX_SEMANTIC_SCHOLAR_API_KEY. The test can also run unauthenticated
against the SS public tier (~100 req/5 min), but defaults to skipping for
CI hygiene when no key is present.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (
        os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
        or os.environ.get("SCILEX_SEMANTIC_SCHOLAR_API_KEY")
    ),
    reason="SEMANTIC_SCHOLAR_API_KEY not set — skip live SS-fallback test",
)

RAG_DOI = "10.48550/arXiv.2005.11401"


@pytest.mark.asyncio
async def test_snowball_with_ss_beats_openalex_alone_for_rag_paper():
    """SS fallback should surface far more forward citations for the RAG
    paper than OpenAlex alone — verifying the audit P3 finding.

    n_oa: forward hits from the OpenAlex-only run
    n_combined: total forward hits from the combined (OA + SS) run
    n_ss: hits in the combined run sourced from SS (provenance in
          {"semantic_scholar", "both"})
    """
    from perspicacite.pipeline.snowball import snowball_expand

    oa_only = await snowball_expand(
        seed_dois=[RAG_DOI],
        direction="forward",
        max_per_seed=100,
        include_semantic_scholar=False,
    )
    combined = await snowball_expand(
        seed_dois=[RAG_DOI],
        direction="forward",
        max_per_seed=100,
        include_semantic_scholar=True,
    )

    n_oa = len(oa_only)
    n_combined = len(combined)
    n_ss = sum(1 for h in combined if h.provenance in {"semantic_scholar", "both"})

    # SS should contribute meaningfully — combined count should be at
    # least 2x OA-only, or at least 30 hits absolute (whichever is
    # smaller — accommodates rate-limited runs).
    threshold = min(n_oa * 2, 30) if n_oa > 0 else 30
    assert n_combined >= threshold, (
        f"combined snowball produced only {n_combined} hits "
        f"(OA-only: {n_oa}, SS contribution: {n_ss}); expected >= {threshold}"
    )
