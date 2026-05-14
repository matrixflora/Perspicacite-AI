"""E2E Scenario A: single-paper round trip (Wave 6.1).

Pipeline: create DKB → add 1 Paper → search → assert retrieval returns
the paper. Verifies chunking + embedding + Chroma round-trip + metadata
preservation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.models.papers import Paper

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_single_paper_round_trip(
    tmp_path: Path, deterministic_embedder, synthetic_paper: Paper,
) -> None:
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )
    cfg = KnowledgeBaseConfig(
        vector_size=deterministic_embedder.dimension,
        chunk_size=500,
        chunk_overlap=50,
        top_k=5,
    )
    kb = DynamicKnowledgeBase(
        vector_store=vs,
        embedding_service=deterministic_embedder,
        config=cfg,
    )
    await kb.initialize()

    added = await kb.add_papers([synthetic_paper])
    assert added >= 1, "should report at least one chunk added"

    # Search for tokens from the abstract — the deterministic embedder
    # is content-blind (hashes the text), so the surest signal is that
    # the *paper_id* survives the round trip.
    hits = await kb.search(query="red giant low metallicity formation rate", top_k=5)
    assert hits, "search should return at least one hit"
    paper_ids = {h["paper_id"] for h in hits}
    assert synthetic_paper.id in paper_ids, (
        f"expected {synthetic_paper.id} in {paper_ids}"
    )

    # Confirm metadata carries the DOI.
    top = next(h for h in hits if h["paper_id"] == synthetic_paper.id)
    meta = top["metadata"]
    assert meta.doi == synthetic_paper.doi
    assert meta.title == synthetic_paper.title

    await kb.cleanup()
