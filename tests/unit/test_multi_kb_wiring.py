"""Tests for multi-KB retriever wiring in basic and contradiction RAG modes."""

import json

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGRequest


def test_build_kb_retriever_single_vs_multi():
    from perspicacite.rag.dynamic_kb import DynamicKnowledgeBase
    from perspicacite.rag.modes.basic import BasicRAGMode
    from perspicacite.retrieval.multi_kb import MultiKBRetriever

    mode = BasicRAGMode(Config())

    single = mode._build_kb_retriever(
        RAGRequest(query="q", kb_name="solo"),
        vector_store=object(),
        embedding_provider=object(),
    )
    assert isinstance(single, DynamicKnowledgeBase)
    assert single.collection_name  # set from "solo"
    assert single._initialized is True

    multi = mode._build_kb_retriever(
        RAGRequest(query="q", kb_names=["a", "b"]),
        vector_store=object(),
        embedding_provider=object(),
    )
    assert isinstance(multi, MultiKBRetriever)
    assert len(multi.kb_metas) == 2

    # kb_names with a single entry behaves as single-KB:
    one = mode._build_kb_retriever(
        RAGRequest(query="q", kb_names=["only"]),
        vector_store=object(),
        embedding_provider=object(),
    )
    assert isinstance(one, DynamicKnowledgeBase)


@pytest.mark.asyncio
async def test_contradiction_multi_kb_source_kb_name(monkeypatch):
    # When _retrieve returns chunks tagged with kb_name (dict form),
    # source events should carry kb_name.
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    mode = ContradictionRAGMode(Config())

    # Fake retrieval returning dicts (the MultiKBRetriever shape):
    # text/score/paper_id/metadata/kb_name
    class _Meta:
        def __init__(self, pid):
            self.paper_id = pid
            self.title = f"T{pid}"
            self.doi = f"10.1/{pid}"
            self.year = 2020

    async def _fake_retrieve(self, request, vs, ep):
        return [
            {
                "text": "X up",
                "score": 0.9,
                "paper_id": "a",
                "metadata": _Meta("a"),
                "kb_name": "kbA",
            },
            {
                "text": "X flat",
                "score": 0.8,
                "paper_id": "b",
                "metadata": _Meta("b"),
                "kb_name": "kbB",
            },
            {
                "text": "X depends",
                "score": 0.7,
                "paper_id": "c",
                "metadata": _Meta("c"),
                "kb_name": "kbA",
            },
        ]

    monkeypatch.setattr(type(mode), "_retrieve", _fake_retrieve)

    class _FakeLLM:
        async def complete(self, messages, **kw):
            return json.dumps(
                {
                    "consensus": ["X exists"],
                    "disagreement": [{"claim": "X up", "papers": ["10.1/a"]}],
                    "open": ["depends"],
                }
            )

    events = [
        ev
        async for ev in mode.execute_stream(
            request=RAGRequest(query="Does X change?", kb_names=["kbA", "kbB"]),
            llm=_FakeLLM(),
            vector_store=object(),
            embedding_provider=object(),
            tools=object(),
        )
    ]
    sources = [json.loads(e.data) for e in events if e.event == "source"]
    assert sources, "expected source events"
    kbs = {s.get("kb_name") for s in sources}
    assert "kbA" in kbs and "kbB" in kbs
    assert all(e.event != "error" for e in events)
