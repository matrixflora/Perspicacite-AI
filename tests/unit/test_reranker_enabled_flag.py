"""Tests for the reranker_enabled master switch + OpenRouter embedding dims.

Context (2026-05-28): a general cross-encoder reranker DEMOTES already-correct
top hits from strong instruction-tuned embedders (Qwen3-Embedding, OpenAI
text-embedding-3-large, codestral-embed). The reranker_enabled flag lets such
configs turn reranking off. See docs/embedding_reranker_policy.md.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.config.schema import RAGModesConfig
from perspicacite.llm.embeddings import LiteLLMEmbeddingProvider
from perspicacite.models.papers import Paper
from perspicacite.rag.resolve_papers import resolve_papers_pipeline


# ---------------------------------------------------------------------------
# Schema: reranker_enabled defaults True, settable False
# ---------------------------------------------------------------------------
def test_reranker_enabled_defaults_true():
    cfg = RAGModesConfig()
    assert cfg.reranker_enabled is True


def test_reranker_enabled_can_disable():
    cfg = RAGModesConfig(reranker_enabled=False)
    assert cfg.reranker_enabled is False


# ---------------------------------------------------------------------------
# Embedding dimension table — OpenRouter prefixed + bare model names
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "model, expected_dim",
    [
        ("openrouter/mistralai/codestral-embed-2505", 1536),
        ("openrouter/qwen/qwen3-embedding-8b", 4096),
        ("openrouter/baai/bge-m3", 1024),
        ("text-embedding-3-large", 3072),
        ("codestral-embed-2505", 1536),
        ("qwen3-embedding-8b", 4096),
        # Unknown model falls back to 1536
        ("openrouter/some/unknown-embed", 1536),
    ],
)
def test_litellm_dimension_table(model, expected_dim):
    provider = LiteLLMEmbeddingProvider(model=model)
    assert provider.dimension == expected_dim


# ---------------------------------------------------------------------------
# resolve_papers_pipeline honours reranker_enabled
# ---------------------------------------------------------------------------
def _app_state(*, reranker_enabled: bool):
    return SimpleNamespace(
        config=SimpleNamespace(
            rag_modes=SimpleNamespace(
                reranker_enabled=reranker_enabled,
                reranker_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
            )
        )
    )


def _paper(title: str) -> Paper:
    return Paper(id=f"doi:10.1/{title}", title=title, abstract="abstract")


@pytest.mark.asyncio
async def test_pipeline_skips_rerank_when_disabled_in_config():
    """reranker_enabled=False → screen_papers_rerank is never called."""
    papers = [_paper("A"), _paper("B")]
    mock_rerank = AsyncMock(return_value=[])
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=papers),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p, **kw: p),
    ), patch(
        "perspicacite.search.screening.screen_papers_rerank", mock_rerank
    ):
        result = await resolve_papers_pipeline(
            query="q",
            databases=["openalex"],
            max_docs=5,
            app_state=_app_state(reranker_enabled=False),
            enrich=False,
            rerank=True,  # caller asks to rerank, but config disables it
        )
    mock_rerank.assert_not_called()
    assert len(result) == 2


@pytest.mark.asyncio
async def test_pipeline_reranks_when_enabled_in_config():
    """reranker_enabled=True → screen_papers_rerank IS called."""
    papers = [_paper("A"), _paper("B")]
    from perspicacite.search.screening import ScreenResult

    fake = [
        ScreenResult(item={"_paper": papers[1], "title": "B", "abstract": "abstract"}, score=0.9, kept=True),
        ScreenResult(item={"_paper": papers[0], "title": "A", "abstract": "abstract"}, score=0.4, kept=True),
    ]
    mock_rerank = AsyncMock(return_value=fake)
    with patch(
        "perspicacite.rag.web_search.run_web_aggregator_search",
        AsyncMock(return_value=papers),
    ), patch(
        "perspicacite.pipeline.enrichment.crossref_enrich.enrich_papers",
        AsyncMock(side_effect=lambda p, **kw: p),
    ), patch(
        "perspicacite.search.screening.screen_papers_rerank", mock_rerank
    ):
        result = await resolve_papers_pipeline(
            query="q",
            databases=["openalex"],
            max_docs=5,
            app_state=_app_state(reranker_enabled=True),
            enrich=False,
            rerank=True,
        )
    mock_rerank.assert_called_once()
    assert result[0].title == "B"  # reranked order
