"""Unit tests for LiteratureSurveyRAGMode KB-context and reference-storage methods.

All ChromaDB, retriever, and session-store calls are mocked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


def _make_mode():
    """Return a LiteratureSurveyRAGMode with default Config (no external services)."""
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    return LiteratureSurveyRAGMode(Config())


def _fake_request(kb_names: list[str], query: str = "protein folding"):
    req = MagicMock()
    req.kb_names = kb_names
    req.kb_name = kb_names[0] if kb_names else "default"
    req.query = query
    return req


# ── _prepare_kb_context ──────────────────────────────────────────────────────

async def test_prepare_kb_context_noop_without_kb_names():
    mode = _make_mode()
    ctx, ids = await mode._prepare_kb_context(
        _fake_request([]), MagicMock(), MagicMock()
    )
    assert ctx == ""
    assert ids == set()


async def test_prepare_kb_context_collects_paper_ids_from_chromadb():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[
            ("doi:10.1/a", "Paper A", 3),
            ("doi:10.1/b", "Paper B", 2),
        ]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        _ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert "doi:10.1/a" in ids
    assert "doi:10.1/b" in ids


async def test_prepare_kb_context_builds_context_block_from_retriever():
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(return_value=[])

    fake_meta = MagicMock()
    fake_meta.title = "AlphaFold"
    fake_meta.year = 2021
    fake_meta.doi = "10.1038/s41586-021-03819-2"
    fake_result = {
        "paper_id": "doi:10.1038/s41586",
        "kb_name": "biology-kb",
        "metadata": fake_meta,
    }

    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[fake_result])
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, _ids = await mode._prepare_kb_context(
            _fake_request(["biology-kb"]), mock_vs, MagicMock()
        )
    assert "AlphaFold" in ctx
    assert "biology-kb" in ctx


async def test_prepare_kb_context_returns_empty_context_on_retrieval_error():
    """Even if retriever raises, known_ids (from ChromaDB listing) should still return."""
    mode = _make_mode()
    mock_vs = AsyncMock()
    mock_vs.list_paper_ids_in_collection = AsyncMock(
        return_value=[("doi:10.1/a", "Paper A", 1)]
    )
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=RuntimeError("embed crash"))
    with patch.object(mode, "_build_kb_retriever", return_value=mock_retriever):
        ctx, ids = await mode._prepare_kb_context(
            _fake_request(["kb-a"]), mock_vs, MagicMock()
        )
    assert ctx == ""          # context block empty on retriever error
    assert "doi:10.1/a" in ids  # IDs still collected from ChromaDB


# ── _store_references_to_all_kbs ─────────────────────────────────────────────

async def test_store_references_noop_without_session_store():
    mode = _make_mode()
    mode.session_store = None
    result = await mode._store_references_to_all_kbs([], ["kb-a", "kb-b"], "query")
    assert result == 0


async def test_store_references_noop_with_single_kb():
    mode = _make_mode()
    mock_store = AsyncMock()
    mode.session_store = mock_store
    result = await mode._store_references_to_all_kbs([], ["kb-a"], "query")
    assert result == 0
    mock_store.store_paper_reference.assert_not_called()


async def test_store_references_skips_primary_kb():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    paper = MagicMock()
    paper.doi = "10.1/test"
    paper.title = "Test Paper"
    paper.authors = ["Author A"]
    paper.year = 2021
    paper.abstract = "Abstract text."

    await mode._store_references_to_all_kbs([paper], ["primary-kb", "extra-kb"], "q")

    call_kb_names = [c.kwargs["kb_name"] for c in mock_store.store_paper_reference.call_args_list]
    assert "primary-kb" not in call_kb_names
    assert "extra-kb" in call_kb_names


async def test_store_references_skips_papers_without_doi():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    paper = MagicMock()
    paper.doi = None
    paper.title = "No DOI Paper"
    paper.authors = []
    paper.year = 2021
    paper.abstract = "Abstract."

    result = await mode._store_references_to_all_kbs([paper], ["kb-a", "kb-b"], "q")
    mock_store.store_paper_reference.assert_not_called()
    assert result == 0


async def test_store_references_returns_correct_count():
    mode = _make_mode()
    mock_store = AsyncMock()
    mock_store.store_paper_reference = AsyncMock(return_value=True)
    mode.session_store = mock_store

    papers = [
        MagicMock(doi="10.1/a", title="A", authors=[], year=2020, abstract=""),
        MagicMock(doi="10.1/b", title="B", authors=[], year=2021, abstract=""),
    ]
    # primary + 2 extra KBs; 2 papers x 2 extra KBs = 4 new rows
    result = await mode._store_references_to_all_kbs(
        papers, ["primary", "extra-1", "extra-2"], "q"
    )
    assert result == 4


async def test_store_references_counts_only_new_rows():
    """store_paper_reference returning False (duplicate) should not increment the count."""
    mode = _make_mode()
    mock_store = AsyncMock()
    # 2 papers x 2 extra KBs = 4 calls; first and third return True, second and fourth return False
    mock_store.store_paper_reference = AsyncMock(side_effect=[True, False, True, False])
    mode.session_store = mock_store

    papers = [
        MagicMock(doi="10.1/a", title="A", authors=[], year=2020, abstract=""),
        MagicMock(doi="10.1/b", title="B", authors=[], year=2021, abstract=""),
    ]
    result = await mode._store_references_to_all_kbs(
        papers, ["primary", "extra-1", "extra-2"], "q"
    )
    assert result == 2  # only 2 new rows, 2 were duplicates


# ── Wiring: execute() calls both new methods ─────────────────────────────────

async def test_execute_calls_prepare_kb_context_and_store_references():
    """execute() should call _prepare_kb_context before search and
    _store_references_to_all_kbs after recommendations."""
    from perspicacite.models.rag import RAGMode, RAGRequest
    from perspicacite.rag.modes.literature_survey import PaperCandidate

    mode = _make_mode()

    prepare_called = []
    store_called = []

    async def fake_prepare(request, vs, ep):
        prepare_called.append(True)
        return ("", set())  # empty context, no known IDs so nothing is filtered

    async def fake_store(papers, kb_names, query):
        store_called.append(True)
        return 0

    # A minimal PaperCandidate to get the pipeline past the empty-list guard
    fake_candidate = PaperCandidate(
        id="doi:10.1/x",
        title="Paper X",
        authors=[],
        year=2021,
        abstract="Abstract text.",
        doi="10.1/x",
    )

    with (
        patch.object(mode, "_prepare_kb_context", side_effect=fake_prepare),
        patch.object(mode, "_store_references_to_all_kbs", side_effect=fake_store),
        patch.object(mode, "_broad_search", new=AsyncMock(return_value=["__marker__"])),
        patch.object(mode, "_convert_to_candidates", return_value=[fake_candidate]),
        patch.object(mode, "_analyze_abstracts_batch", new=AsyncMock(return_value=[])),
        patch.object(mode, "_generate_recommendations", new=AsyncMock(return_value=None)),
    ):
        request = RAGRequest(
            query="protein folding",
            mode=RAGMode.LITERATURE_SURVEY,
            kb_name="kb-a",
            kb_names=["kb-a", "kb-b"],
        )
        await mode.execute(
            request=request,
            llm=AsyncMock(),
            vector_store=AsyncMock(),
            embedding_provider=AsyncMock(),
            tools=MagicMock(),
        )

    assert prepare_called, "_prepare_kb_context was not called"
    assert store_called, "_store_references_to_all_kbs was not called"


async def test_execute_stream_calls_prepare_kb_context_and_store_references():
    """execute_stream() should call _prepare_kb_context and _store_references_to_all_kbs."""
    from perspicacite.models.rag import RAGMode, RAGRequest
    from perspicacite.rag.modes.literature_survey import PaperCandidate

    mode = _make_mode()

    prepare_called = []
    store_called = []

    async def fake_prepare(request, vs, ep):
        prepare_called.append(True)
        return ("", set())

    async def fake_store(papers, kb_names, query):
        store_called.append(True)
        return 0

    fake_candidate = PaperCandidate(
        id="doi:10.1/x",
        title="Paper X",
        authors=[],
        year=2021,
        abstract="Abstract text.",
        doi="10.1/x",
    )

    with (
        patch.object(mode, "_prepare_kb_context", side_effect=fake_prepare),
        patch.object(mode, "_store_references_to_all_kbs", side_effect=fake_store),
        patch.object(mode, "_broad_search", new=AsyncMock(return_value=["__marker__"])),
        patch.object(mode, "_convert_to_candidates", return_value=[fake_candidate]),
        patch.object(mode, "_analyze_abstracts_batch", new=AsyncMock(return_value=[])),
        patch.object(mode, "_generate_recommendations", new=AsyncMock(return_value=None)),
    ):
        request = RAGRequest(
            query="protein folding",
            mode=RAGMode.LITERATURE_SURVEY,
            kb_name="kb-a",
            kb_names=["kb-a", "kb-b"],
        )
        # Drain the async generator
        events = []
        async for event in mode.execute_stream(
            request=request,
            llm=AsyncMock(),
            vector_store=AsyncMock(),
            embedding_provider=AsyncMock(),
            tools=MagicMock(),
        ):
            events.append(event)

    assert prepare_called, "_prepare_kb_context was not called in execute_stream()"
    assert store_called, "_store_references_to_all_kbs was not called in execute_stream()"
