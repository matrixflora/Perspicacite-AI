# tests/unit/test_basic_provenance.py
from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config, KnowledgeBaseConfig, RAGModesConfig
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting
from perspicacite.rag.modes.basic import BasicRAGMode


def _cfg() -> Config:
    cfg = MagicMock(spec=Config)
    cfg.rag_modes = MagicMock(spec=RAGModesConfig)
    cfg.rag_modes.basic = MagicMock()
    cfg.rag_modes.basic.model_dump = lambda: {"use_hybrid": True}
    cfg.knowledge_base = MagicMock(spec=KnowledgeBaseConfig)
    cfg.knowledge_base.default_top_k = 10
    cfg.knowledge_base.use_two_pass = False  # use legacy chunk-level path; simpler to mock
    return cfg


async def _aiter_stream(*chunks: str) -> AsyncIterator[str]:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_basic_pushes_retrieval_and_trace(monkeypatch) -> None:
    cfg = _cfg()
    mode = BasicRAGMode(cfg)

    fake_chunks = [
        {"paper_id": "p1", "score": 0.9, "kb_name": "kb1",
         "metadata": MagicMock(doi="10.1/a", title="A", authors="X", year=2024, paper_id="p1"),
         "text": "snippet"},
        {"paper_id": "p2", "score": 0.7, "kb_name": "kb1",
         "metadata": MagicMock(doi="10.1/b", title="B", authors="Y", year=2020, paper_id="p2"),
         "text": "snippet2"},
    ]
    retriever = MagicMock()
    retriever.collection_name = "kb_kb1"
    retriever.search = AsyncMock(return_value=fake_chunks)
    retriever._initialized = True
    mode._build_kb_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    # Short-circuit scope resolution + compute_retrieval_query
    from perspicacite.rag import query_scope, conversation_helpers
    monkeypatch.setattr(query_scope, "resolve_paper_scope_for_query",
                        AsyncMock(return_value=MagicMock(scope_note="")))
    monkeypatch.setattr(conversation_helpers, "compute_retrieval_query",
                        AsyncMock(return_value=("q", None)))

    llm = MagicMock()
    llm.stream = MagicMock(return_value=_aiter_stream("answer"))

    req = RAGRequest(query="q", mode=RAGMode.BASIC, kb_name="kb1")
    c = ProvenanceCollector(conversation_id="c", message_id="m",
                            rag_mode="basic", request_params={})
    with collecting(c):
        events: list[StreamEvent] = []
        async for ev in mode.execute_stream(req, llm, MagicMock(), MagicMock(), MagicMock()):
            events.append(ev)

    assert len(c.retrieval_events) == 2
    assert c.retrieval_events[0].doi == "10.1/a"
    assert c.retrieval_events[0].kb_name == "kb1"
    assert c.retrieval_events[0].stage_label == "basic.retrieve"
    steps = [t["step"] for t in c.mode_trace]
    assert "retrieve" in steps
