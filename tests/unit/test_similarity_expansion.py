"""Two-phase similarity-expansion orchestrator + reference assembly."""

from types import SimpleNamespace

import pytest

import perspicacite.pipeline.similarity_expansion as se
from perspicacite.pipeline.similarity_expansion import (
    _score_histogram,
    build_reference_papers,
    commit_expansion,
    score_expansion_candidates,
)

# ---- Task 3: reference assembly ----


class _StoreMixed:
    async def list_paper_metadata(self, collection):
        return [
            {"paper_id": "p1", "abstract": "abstract one"},
            {"paper_id": "p2", "abstract": None},   # -> chunks
        ]
    async def list_paper_chunks(self, collection, max_per_paper=20):
        return {"p2": ["chunk a", "chunk b"]}


@pytest.mark.asyncio
async def test_build_reference_papers_mixes_abstract_and_chunks():
    papers, n_abs, n_fb = await build_reference_papers(_StoreMixed(), "kb")
    assert papers == [["abstract one"], ["chunk a", "chunk b"]]
    assert n_abs == 1 and n_fb == 1


class _StoreAllAbstracts:
    async def list_paper_metadata(self, collection):
        return [{"paper_id": "p1", "abstract": "a one"}, {"paper_id": "p2", "abstract": "a two"}]
    async def list_paper_chunks(self, collection, max_per_paper=20):
        raise AssertionError("must not fetch chunks when all papers have abstracts")


@pytest.mark.asyncio
async def test_build_reference_papers_skips_chunks_when_all_abstracts():
    papers, n_abs, n_fb = await build_reference_papers(_StoreAllAbstracts(), "kb")
    assert papers == [["a one"], ["a two"]] and n_abs == 2 and n_fb == 0


# ---- Task 4: phase 1 ----


def _hit(doi, title, abstract):
    return SimpleNamespace(
        expanded_doi=doi, seed_doi="10.1/seed", direction="forward",
        title=title, year=2024, citation_count=1, abstract=abstract,
        authors=["A. Author"], journal="J", provenance="openalex",
    )


class _Embedder:
    async def embed(self, texts):
        return [[1.0, 0.0] if "relevant" in t.lower() else [0.0, 1.0] for t in texts]


class _OrchStore:
    async def search(self, collection, query_embedding, top_k=5, **kw):
        score = 0.9 if query_embedding[0] > query_embedding[1] else 0.2
        return [SimpleNamespace(score=score) for _ in range(top_k)]

    async def paper_exists(self, collection, doi):
        return doi == "10.1/already"

    async def list_paper_metadata(self, collection):
        return [{"paper_id": "seed", "doi": "10.1/seed", "abstract": "relevant graph neural networks"}]

    async def list_paper_chunks(self, collection, max_per_paper=20):
        return {}


async def _kb_meta(name):
    return SimpleNamespace(collection_name="kb_collection", description="GNNs")


def _app_state():
    return SimpleNamespace(
        session_store=SimpleNamespace(get_kb_metadata=_kb_meta),
        vector_store=_OrchStore(),
        embedding_provider=_Embedder(),
        config=SimpleNamespace(pdf_download=SimpleNamespace(unpaywall_email="me@x.org")),
        llm_client=None,
    )


def test_score_histogram_buckets():
    h = _score_histogram([0.05, 0.15, 0.95, 0.96], bins=10)
    assert sum(b["count"] for b in h) == 4
    assert len(h) == 10
    assert h[0]["count"] == 1 and h[-1]["count"] == 2


@pytest.mark.asyncio
async def test_score_expansion_filters_existing_and_scores(monkeypatch):
    hits = [
        _hit("10.1/relevant", "Relevant", "relevant content"),
        _hit("10.1/offtopic", "Off", "tax accounting"),
        _hit("10.1/already", "Already", "relevant but present"),
    ]

    async def _fake_snowball(**kwargs):
        return hits

    monkeypatch.setattr(se, "snowball_expand", _fake_snowball)

    report = await score_expansion_candidates(
        app_state=_app_state(), kb_name="kb1", direction="forward", method="embedding",
    )
    dois = {c["doi"] for c in report.candidates}
    assert "10.1/already" not in dois  # dropped (already in KB)
    assert {"10.1/relevant", "10.1/offtopic"} <= dois
    rel = next(c for c in report.candidates if c["doi"] == "10.1/relevant")
    off = next(c for c in report.candidates if c["doi"] == "10.1/offtopic")
    assert rel["score"] > off["score"]
    assert report.seed_count == 1
    assert len(report.samples) == 2  # <= n -> all
    assert sum(b["count"] for b in report.histogram) == 2


@pytest.mark.asyncio
async def test_score_expansion_no_seeds(monkeypatch):
    app_state = _app_state()

    async def _no_rows(collection):
        return []

    app_state.vector_store.list_paper_metadata = _no_rows
    report = await score_expansion_candidates(
        app_state=app_state, kb_name="kb1", method="embedding"
    )
    assert report.candidates == [] and report.seed_count == 0


# ---- Task 5: phase 2 ----


@pytest.mark.asyncio
async def test_commit_ingests_only_above_cutoff(monkeypatch):
    scored = [
        {"doi": "10.1/keep", "title": "K", "score": 0.8},
        {"doi": "10.1/drop", "title": "D", "score": 0.2},
        {"doi": None, "title": "no doi", "score": 0.9},
    ]
    captured: dict = {}

    async def _fake_ingest(app_state, kb_name, dois, **kw):
        captured["dois"] = dois
        return {"added_papers": len(dois), "added_chunks": 7, "failed": [], "pdf_download": {}}

    monkeypatch.setattr(se, "ingest_dois_into_kb", _fake_ingest)
    res = await commit_expansion(
        app_state=SimpleNamespace(), kb_name="kb1", scored=scored, cutoff=0.5
    )
    assert captured["dois"] == ["10.1/keep"]
    assert res["added_papers"] == 1 and res["kept"] == 1


@pytest.mark.asyncio
async def test_commit_nothing_above_cutoff_skips_ingest(monkeypatch):
    called = {"n": 0}

    async def _fake_ingest(app_state, kb_name, dois, **kw):
        called["n"] += 1
        return {}

    monkeypatch.setattr(se, "ingest_dois_into_kb", _fake_ingest)
    res = await commit_expansion(
        app_state=SimpleNamespace(), kb_name="kb1",
        scored=[{"doi": "10.1/x", "score": 0.1}], cutoff=0.5,
    )
    assert called["n"] == 0 and res["kept"] == 0
