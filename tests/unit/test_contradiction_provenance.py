from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting
from perspicacite.rag.modes.contradiction import ContradictionRAGMode


def _cfg() -> Config:
    cfg = MagicMock(spec=Config)
    cfg.rag_modes = MagicMock()
    cfg.rag_modes.contradiction = MagicMock()
    return cfg


async def _astream(*chunks: str) -> AsyncIterator[str]:
    for c in chunks:
        yield c


@pytest.mark.asyncio
async def test_contradiction_pushes_events() -> None:
    mode = ContradictionRAGMode(_cfg())

    fake_chunks = [
        {"paper_id": f"p{i}", "score": 0.9 - i * 0.1, "kb_name": "kb1", "text": "t",
         "metadata": {"doi": f"10.1/{i}", "title": f"T{i}", "year": 2023,
                      "content_type": "full_text", "content_source": "pmc"}}
        for i in range(4)
    ]
    mode._retrieve = AsyncMock(return_value=fake_chunks)  # type: ignore[method-assign]

    # Short-circuit the LLM-heavy helpers
    mode._summarize_claims = AsyncMock(return_value={"p0": ["claim A"], "p1": ["claim B"],
                                                     "p2": ["claim C"], "p3": ["claim D"]})  # type: ignore[method-assign]
    mode._cluster_claims = AsyncMock(return_value={
        "agreement": [["p0", "p1"]],
        "disagreement": [["p2", "p3"]],
        "open": [],
    })  # type: ignore[method-assign]

    async def _synth_stream(*a, **kw):
        yield StreamEvent.content("synth")
    mode._synthesize_stream = _synth_stream  # type: ignore[method-assign]

    req = RAGRequest(query="q", mode=RAGMode.CONTRADICTION, kb_name="kb1")
    c = ProvenanceCollector(conversation_id="c", message_id="m",
                            rag_mode="contradiction", request_params={})
    with collecting(c):
        async for _ in mode.execute_stream(req, MagicMock(), MagicMock(), MagicMock(), MagicMock()):
            pass

    assert len(c.retrieval_events) == 4
    assert c.retrieval_events[0].stage_label == "contradiction.retrieve"
    steps = [t["step"] for t in c.mode_trace]
    assert "retrieve" in steps
    assert "cluster" in steps
