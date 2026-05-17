"""Unit tests for ContradictionRAGMode."""

import json

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest


class _Chunk:
    """Fake chunk that mimics dkb.search() dict result."""

    def __init__(self, pid, text, score=0.9, title=None, doi=None, year=2020):
        self.metadata = {
            "paper_id": pid,
            "title": title or f"Paper {pid}",
            "doi": doi or f"10.1/{pid}",
            "year": year,
        }
        self.text = text
        self.score = score


class _FakeLLM:
    def __init__(self, responses=None):
        # responses: list of strings returned in order; if exhausted, returns last
        self._responses = responses or [
            '{"consensus": ["all agree X exists"], '
            '"disagreement": [{"claim": "X increases Y", "papers": ["10.1/a"]}, '
            '{"claim": "X has no effect on Y", "papers": ["10.1/b"]}], '
            '"open": ["dependence on condition Z"]}'
        ]
        self._i = 0

    async def complete(self, messages, **kw):
        r = self._responses[min(self._i, len(self._responses) - 1)]
        self._i += 1
        return r


def _collect_text(events):
    out = ""
    for e in events:
        if e.event == "content":
            try:
                data = json.loads(e.data)
                out += data.get("delta", "") or data.get("content", "") or ""
            except Exception:
                out += str(e.data)
    return out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contradiction_three_buckets(monkeypatch):
    """Full path: 3 papers → three-bucket analysis, no error events."""
    ContradictionRAGMode = __import__(
        "perspicacite.rag.modes.contradiction", fromlist=["ContradictionRAGMode"]
    ).ContradictionRAGMode
    mode = ContradictionRAGMode(Config())
    chunks = [
        _Chunk("a", "X increases Y in our study"),
        _Chunk("b", "X has no significant effect on Y"),
        _Chunk("c", "X may increase Y only under condition Z"),
    ]

    # Provide 3 LLM responses:
    # 1st: per-paper claim summaries (for paper "a", "b", "c")
    # 2nd: same (paper b)
    # 3rd: same (paper c)
    # 4th: cluster JSON
    # 5th: synthesis text
    claim_summary = "- X affects Y in this study"
    cluster_json = json.dumps(
        {
            "consensus": ["all studies agree X is present"],
            "disagreement": [
                {"claim": "X increases Y", "papers": ["10.1/a"]},
                {"claim": "X has no effect", "papers": ["10.1/b"]},
            ],
            "open": ["role of condition Z"],
        }
    )
    synthesis_text = (
        "## Points of consensus\nAll agree X exists.\n\n"
        "## Points of disagreement\nPaper A claims X increases Y; Paper B disagrees.\n\n"
        "## Open / under-determined\nCondition Z is unclear."
    )

    fake_llm = _FakeLLM(
        responses=[
            claim_summary,  # paper a
            claim_summary,  # paper b
            claim_summary,  # paper c
            cluster_json,  # clustering
            synthesis_text,  # synthesis
        ]
    )

    async def _fake_retrieve(self, request, vs, ep):
        return chunks

    monkeypatch.setattr(type(mode), "_retrieve", _fake_retrieve)

    events = []
    async for ev in mode.execute_stream(
        request=RAGRequest(query="Does X affect Y?", kb_name="k"),
        llm=fake_llm,
        vector_store=object(),
        embedding_provider=object(),
        tools=object(),
    ):
        events.append(ev)

    kinds = [e.event for e in events]
    assert "content" in kinds, "Expected content events"
    assert "error" not in kinds, f"Unexpected error event in {kinds}"
    assert "source" in kinds, "Expected source events"
    assert "done" in kinds, "Expected done event"

    text = _collect_text(events).lower()
    assert "consensus" in text or "agree" in text
    assert "disagree" in text or "disagreement" in text


@pytest.mark.asyncio
async def test_contradiction_few_papers_degrades(monkeypatch):
    """Fewer than 3 papers → note + fallback answer, no error events."""
    ContradictionRAGMode = __import__(
        "perspicacite.rag.modes.contradiction", fromlist=["ContradictionRAGMode"]
    ).ContradictionRAGMode
    mode = ContradictionRAGMode(Config())

    async def _fake_retrieve(self, request, vs, ep):
        return []  # zero papers

    monkeypatch.setattr(type(mode), "_retrieve", _fake_retrieve)

    events = [
        ev
        async for ev in mode.execute_stream(
            request=RAGRequest(query="q", kb_name="k"),
            llm=_FakeLLM(responses=["a normal answer"]),
            vector_store=object(),
            embedding_provider=object(),
            tools=object(),
        )
    ]

    kinds = [e.event for e in events]
    assert "error" not in kinds, f"Unexpected error: {kinds}"
    assert "content" in kinds, "Expected content events"
    assert "done" in kinds, "Expected done event"

    text = _collect_text(events)
    # Should contain the degradation note mentioning the mode falls back
    assert (
        "normal" in text.lower()
        or "0" in text
        or "note" in text.lower()
        or "answering" in text.lower()
    )


@pytest.mark.asyncio
async def test_contradiction_two_papers_degrades(monkeypatch):
    """2 papers (< MIN_PAPERS=3) → note + fallback, no error."""
    ContradictionRAGMode = __import__(
        "perspicacite.rag.modes.contradiction", fromlist=["ContradictionRAGMode"]
    ).ContradictionRAGMode
    mode = ContradictionRAGMode(Config())
    chunks = [
        _Chunk("a", "Some text about X"),
        _Chunk("b", "More text about X"),
    ]

    async def _fake_retrieve(self, request, vs, ep):
        return chunks

    monkeypatch.setattr(type(mode), "_retrieve", _fake_retrieve)

    events = [
        ev
        async for ev in mode.execute_stream(
            request=RAGRequest(query="q?", kb_name="k"),
            llm=_FakeLLM(responses=["fallback answer text"]),
            vector_store=object(),
            embedding_provider=object(),
            tools=object(),
        )
    ]
    kinds = [e.event for e in events]
    assert "error" not in kinds
    assert "content" in kinds
    assert "done" in kinds


@pytest.mark.asyncio
async def test_contradiction_engine_routing():
    """RAGEngine._get_mode_handler(RAGMode.CONTRADICTION) returns ContradictionRAGMode."""
    from perspicacite.rag.engine import RAGEngine
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    eng = RAGEngine(
        llm_client=None,
        vector_store=None,
        embedding_provider=None,
        tool_registry=None,
        config=Config(),
    )
    handler = eng._get_mode_handler(RAGMode.CONTRADICTION)
    assert isinstance(handler, ContradictionRAGMode)


@pytest.mark.asyncio
async def test_contradiction_execute_returns_response(monkeypatch):
    """execute() returns a RAGResponse with mode=CONTRADICTION and non-empty answer."""
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    mode = ContradictionRAGMode(Config())

    async def _fake_retrieve(self, request, vs, ep):
        return [
            _Chunk("a", "X up"),
            _Chunk("b", "X flat"),
            _Chunk("c", "X depends"),
        ]

    monkeypatch.setattr(ContradictionRAGMode, "_retrieve", _fake_retrieve)

    claim_summary = "- X is relevant"
    cluster_json = json.dumps(
        {
            "consensus": ["X exists"],
            "disagreement": [{"claim": "X increases", "papers": ["10.1/a"]}],
            "open": ["conditions"],
        }
    )
    synthesis = "## Points of consensus\nX exists.\n\n## Points of disagreement\nSee above.\n\n## Open\nConditions unclear."

    fake_llm = _FakeLLM(
        responses=[claim_summary, claim_summary, claim_summary, cluster_json, synthesis]
    )

    resp = await mode.execute(
        request=RAGRequest(query="Does X affect Y?", kb_name="k"),
        llm=fake_llm,
        vector_store=object(),
        embedding_provider=object(),
        tools=object(),
    )
    assert resp.mode == RAGMode.CONTRADICTION
    assert isinstance(resp.answer, str) and len(resp.answer) > 0


@pytest.mark.asyncio
async def test_contradiction_error_resilience(monkeypatch):
    """LLM failure inside mode → stream must not raise; yields content events gracefully."""
    from perspicacite.rag.modes.contradiction import ContradictionRAGMode

    mode = ContradictionRAGMode(Config())

    class _BrokenLLM:
        async def complete(self, messages, **kw):
            raise RuntimeError("LLM exploded")

    async def _fake_retrieve(self, request, vs, ep):
        return [
            _Chunk("a", "text a"),
            _Chunk("b", "text b"),
            _Chunk("c", "text c"),
        ]

    monkeypatch.setattr(ContradictionRAGMode, "_retrieve", _fake_retrieve)

    # Must not raise — collect all events without exception
    events = [
        ev
        async for ev in mode.execute_stream(
            request=RAGRequest(query="q?", kb_name="k"),
            llm=_BrokenLLM(),
            vector_store=object(),
            embedding_provider=object(),
            tools=object(),
        )
    ]
    kinds = [e.event for e in events]
    # The stream must complete (done or error), never raise
    assert "error" in kinds or "done" in kinds, f"Expected done or error event; got {kinds}"
    # Must not have raised — if we reach here the generator completed cleanly


@pytest.mark.asyncio
async def test_contradiction_exported_from_modes():
    """ContradictionRAGMode is exported from perspicacite.rag.modes."""
    from perspicacite.rag.modes import ContradictionRAGMode

    assert ContradictionRAGMode is not None
