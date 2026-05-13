"""Multi-KB retrieval: fan a query across several KB collections, merge + dedup."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from perspicacite.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = get_logger("perspicacite.retrieval.multi_kb")


def check_embedding_compat(kb_metas: Sequence[Any]) -> str | None:
    """Return None if all KBs share an embedding model; else a message listing the models."""
    models = {getattr(m, "embedding_model", None) for m in kb_metas}
    models.discard(None)
    if len(models) <= 1:
        return None
    return (
        "Cannot query these knowledge bases together: they use different embedding models "
        f"({', '.join(sorted(str(m) for m in models))}). Re-embedding is not supported."
    )


class MultiKBRetriever:
    """Fan a query across multiple KB collections, merge results, and dedup by paper_id.

    Quacks like the DynamicKnowledgeBase slice that RAG modes use: `.search`,
    `.search_two_pass`, and tolerant `.collection_name` / `._initialized` attrs.
    """

    def __init__(
        self,
        vector_store: Any,
        embedding_service: Any,
        kb_metas: Sequence[Any],
        default_top_k: int = 10,
        default_min_score: float = 0.0,
    ):
        self.vector_store = vector_store
        self.embedding_service = embedding_service
        self.kb_metas = list(kb_metas)
        self.default_top_k = default_top_k
        self.default_min_score = default_min_score
        # tolerate modes assigning these:
        self.collection_name = "+".join(getattr(m, "collection_name", "") for m in self.kb_metas)
        self._initialized = True

    async def search(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        top_k = top_k or self.default_top_k
        min_score = self.default_min_score if min_score is None else min_score
        query_embeddings = await self.embedding_service.embed([query])
        query_embedding = query_embeddings[0]
        merged: dict[str, dict[str, Any]] = {}  # paper_id -> best dict
        orderless: list[dict[str, Any]] = []
        for meta in self.kb_metas:
            coll = getattr(meta, "collection_name", None)
            if not coll:
                continue
            try:
                results = await self.vector_store.search(
                    collection=coll,
                    query_embedding=query_embedding,
                    top_k=top_k * 2,
                )
            except Exception as e:
                logger.warning("multi_kb_search_collection_failed", collection=coll, error=str(e))
                continue
            kb_display = getattr(meta, "name", None) or coll
            for r in results:
                chunk = getattr(r, "chunk", None)
                meta_obj = getattr(chunk, "metadata", None) if chunk is not None else None
                pid = getattr(meta_obj, "paper_id", None)
                score = float(getattr(r, "score", 0.0) or 0.0)
                if score < min_score:
                    continue
                d = {
                    "text": getattr(chunk, "text", "") if chunk is not None else "",
                    "score": score,
                    "paper_id": pid,
                    "metadata": meta_obj,
                    "kb_name": kb_display,
                }
                if pid:
                    prev = merged.get(pid)
                    if prev is None or score > prev["score"]:
                        merged[pid] = d
                else:
                    orderless.append(d)
        combined = list(merged.values()) + orderless
        combined.sort(key=lambda x: x["score"], reverse=True)
        result = combined[:top_k]
        logger.info(
            "multi_kb_search",
            kbs=[getattr(m, "name", "?") for m in self.kb_metas],
            hits=len(result),
        )
        return result

    async def search_two_pass(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        **_: Any,
    ) -> list[dict[str, Any]]:
        # v1: just delegate. (Two-pass paper-level expansion across KBs is a future refinement.)
        return await self.search(query, top_k=top_k, min_score=min_score)


async def query_chunks_across_collections(
    *,
    vector_store: Any,
    embedding_service: Any,
    collection_names: list[str],
    query: str,
    top_k: int,
    min_score: float = 0.0,
) -> list[dict[str, Any]]:
    """Fan vector_store.search across collections, merge by paper_id (best score),
    tag each hit with kb_name (= collection_name), sort, return top_k."""
    if not collection_names:
        return []
    query_embedding = (await embedding_service.embed([query]))[0]

    async def _one(coll: str) -> list[Any]:
        try:
            results: list[Any] = await vector_store.search(
                collection=coll, query_embedding=query_embedding, top_k=top_k * 2
            )
            return results
        except Exception as e:
            logger.warning("fanout_search_failed", collection=coll, error=str(e))
            return []

    per = await asyncio.gather(*(_one(c) for c in collection_names))
    merged: dict[str, dict[str, Any]] = {}
    orderless: list[dict[str, Any]] = []
    for coll, hits in zip(collection_names, per, strict=False):
        for r in hits:
            chunk = getattr(r, "chunk", None)
            meta = getattr(chunk, "metadata", None) if chunk is not None else None
            pid = getattr(meta, "paper_id", None)
            score = float(getattr(r, "score", 0.0) or 0.0)
            if score < min_score:
                continue
            d = {
                "text": getattr(chunk, "text", "") if chunk is not None else "",
                "score": score,
                "paper_id": pid,
                "metadata": meta,
                "kb_name": coll,
            }
            if pid:
                prev = merged.get(pid)
                if prev is None or score > prev["score"]:
                    merged[pid] = d
            else:
                orderless.append(d)
    combined = list(merged.values()) + orderless
    combined.sort(key=lambda x: x["score"], reverse=True)
    return combined[:top_k]


async def get_chunks_by_paper_ids_across(
    vector_store: Any,
    *,
    collection_names: list[str],
    paper_ids: list[str],
) -> list[Any]:
    """Fan get_chunks_by_paper_ids across collections in parallel.
    Returns concatenated DocumentChunk list (caller dedups if needed)."""
    if not collection_names or not paper_ids:
        return []

    async def _one(coll: str) -> list[Any]:
        try:
            chunks: list[Any] = await vector_store.get_chunks_by_paper_ids(coll, paper_ids)
            return chunks
        except Exception as e:
            logger.warning("fanout_get_chunks_failed", collection=coll, error=str(e))
            return []

    per = await asyncio.gather(*(_one(c) for c in collection_names))
    out: list[Any] = []
    for chunks in per:
        out.extend(chunks)
    return out
