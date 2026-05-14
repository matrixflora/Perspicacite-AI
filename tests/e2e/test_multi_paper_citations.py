"""E2E Scenario B: multi-paper + citation expansion (Wave 6.1).

Pipeline:
  - Build KB
  - Ingest 5 Paper objects via add_papers (real chunking + embedding)
  - Emit KB-log events (Wave 4.3 contract) for the 5 papers + 1 expanded
    citation, including a paper_skipped + a paper_failed event
  - Read the log back and verify all events round-trip with stable
    types (KBEvent dataclasses) and counts

  - Search returns at least one of the astro papers for an astro query.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.models.papers import Paper

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_multi_paper_with_citations(
    tmp_path: Path, deterministic_embedder, synthetic_corpus: list[Paper],
) -> None:
    from perspicacite.pipeline.kb_log import KBEvent, KBLogWriter
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    log_path = tmp_path / "kb_logs" / "multi.jsonl"
    log = KBLogWriter(path=log_path)
    # First event the contract expects.
    log.append(KBEvent(event="kb_created", kb_name="multi", source_command="test"))

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

    added = await kb.add_papers(synthetic_corpus)
    assert added >= 5  # at least one chunk per paper

    # Emit synthetic KB-log entries (in production this is the job of
    # ingest_dois_into_kb / expand_kb_via_citations).
    for p in synthetic_corpus:
        log.append(KBEvent(
            event="paper_added", kb_name="multi", paper_id=p.id,
            title=p.title, chunks=3, source_command="test",
        ))
    # Simulated citation-expansion adds 1 ref:
    log.append(KBEvent(
        event="paper_added", kb_name="multi",
        paper_id="doi:10.0099/expanded-ref",
        title="Cited reference (from expansion)",
        chunks=2, source_command="expand_citations",
    ))
    # And one skipped + one failed, exercising the full EventKind set.
    log.append(KBEvent(
        event="paper_skipped", kb_name="multi",
        paper_id="doi:10.0099/dup", reason="already in KB",
    ))
    log.append(KBEvent(
        event="paper_failed", kb_name="multi",
        paper_id="doi:10.0099/broken", reason="fetcher timeout",
    ))

    # Read the log back: KBLogWriter.read_all() returns list[KBEvent]
    events = log.read_all()
    added_events = [e for e in events if e.event == "paper_added"]
    assert len(added_events) == 6, (
        f"expected 6 paper_added events, got {len(added_events)}"
    )
    assert any("expanded-ref" in e.paper_id for e in added_events)
    assert any(e.event == "paper_skipped" for e in events)
    assert any(e.event == "paper_failed" for e in events)
    # First event was kb_created.
    assert events[0].event == "kb_created"

    # Retrieval reach: stellar query returns at least one astro paper.
    hits = await kb.search(query="stellar nucleosynthesis supernova ejecta", top_k=5)
    paper_ids = {h["paper_id"] for h in hits}
    assert any(pid.endswith("a1") or pid.endswith("a2") for pid in paper_ids), (
        f"expected an astro paper in {paper_ids}"
    )

    await kb.cleanup()
