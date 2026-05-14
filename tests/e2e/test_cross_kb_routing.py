"""E2E Scenario C: cross-KB routing (Wave 6.1).

Builds two KBs with topically-distinct descriptions and verifies that
``auto_route_kbs(method="bm25")`` picks the right one for two queries.
The BM25 path needs no LLM — it scores against per-KB description +
sampled paper titles.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.models.kb import ChunkConfig, KnowledgeBase
from perspicacite.models.papers import Paper

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_cross_kb_routing_picks_relevant_kb(
    tmp_path: Path, deterministic_embedder, synthetic_corpus: list[Paper],
) -> None:
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase, KnowledgeBaseConfig
    from perspicacite.rag.kb_router import auto_route_kbs
    from perspicacite.retrieval.chroma_store import ChromaVectorStore

    vs = ChromaVectorStore(
        persist_dir=str(tmp_path / "chroma"),
        embedding_provider=deterministic_embedder,
    )

    kb_cfg = KnowledgeBaseConfig(
        vector_size=deterministic_embedder.dimension,
        chunk_size=500, chunk_overlap=50, top_k=5,
    )
    astro_kb = DynamicKnowledgeBase(
        vector_store=vs, embedding_service=deterministic_embedder, config=kb_cfg,
    )
    bio_kb = DynamicKnowledgeBase(
        vector_store=vs, embedding_service=deterministic_embedder, config=kb_cfg,
    )
    await astro_kb.initialize()
    await bio_kb.initialize()

    astro_papers = [p for p in synthetic_corpus if "a" in p.doi.split("/")[1]]
    bio_papers = [p for p in synthetic_corpus if "b" in p.doi.split("/")[1]]
    await astro_kb.add_papers(astro_papers)
    await bio_kb.add_papers(bio_papers)

    # Router metadata — these descriptions are the strongest BM25 signal.
    astro_meta = KnowledgeBase(
        name="astro",
        description=(
            "Stellar physics, supernova nucleosynthesis, red giants, "
            "stellar evolution, metallicity, nuclear burning shells."
        ),
        collection_name=astro_kb.collection_name,
        embedding_model=deterministic_embedder.model_name,
        chunk_config=ChunkConfig(),
    )
    bio_meta = KnowledgeBase(
        name="bio",
        description=(
            "Protein folding, AlphaFold structure predictions, cryo-EM, "
            "ribosome biogenesis, transmembrane domains, GPCR receptors."
        ),
        collection_name=bio_kb.collection_name,
        embedding_model=deterministic_embedder.model_name,
        chunk_config=ChunkConfig(),
    )
    metas = [astro_meta, bio_meta]

    hits_astro = await auto_route_kbs(
        query="how do red giants form in low-metallicity environments?",
        kb_metas=metas, vector_store=vs, method="bm25", top_k=2,
    )
    assert hits_astro, "router returned no hits for astro query"
    assert hits_astro[0].kb_name == "astro", (
        f"astro should rank first; got {[(h.kb_name, h.score) for h in hits_astro]}"
    )

    hits_bio = await auto_route_kbs(
        query="how does AlphaFold predict GPCR protein transmembrane structure?",
        kb_metas=metas, vector_store=vs, method="bm25", top_k=2,
    )
    assert hits_bio, "router returned no hits for bio query"
    assert hits_bio[0].kb_name == "bio", (
        f"bio should rank first; got {[(h.kb_name, h.score) for h in hits_bio]}"
    )

    await astro_kb.cleanup()
    await bio_kb.cleanup()
