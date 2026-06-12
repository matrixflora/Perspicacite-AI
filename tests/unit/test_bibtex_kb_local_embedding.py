"""Regression: BibTeX KB ingest must route the configured embedding model
through ``create_embedding_provider`` (not hardcode ``LiteLLMEmbeddingProvider``).

When the configured ``embedding_model`` is a local SentenceTransformer model
(e.g. ``all-MiniLM-L6-v2``), LiteLLM rejects it with "LLM Provider NOT
provided" and every paper fails to embed — ``create-kb`` / ``add-to-kb`` then
silently produce a KB with **0 chunks**. The fix is to build the provider via
``perspicacite.llm.embeddings.create_embedding_provider``, which routes
``all-*`` / bare local model names to ``SentenceTransformerEmbeddingProvider``
while keeping LiteLLM for API models.

This fix has regressed at the call site at least twice, so these tests pin it:

* ``test_..._uses_routing_factory_*`` mocks the heavy collaborators and asserts
  the provider built inside ``create_kb_from_bibtex`` is a
  ``SentenceTransformerEmbeddingProvider``. It runs offline / fast and fails the
  instant the call site reverts to ``LiteLLMEmbeddingProvider(...)``.
* ``test_..._produces_chunks`` is the end-to-end gold standard: it builds a tiny
  KB with ``embedding_model: all-MiniLM-L6-v2`` and asserts ``chunks_added > 0``.
  With the bug present this returns 0 (the embed error is swallowed per-paper in
  ``DynamicKnowledgeBase.add_papers``). It skips if local embedding is
  unavailable in the environment.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from perspicacite.config.schema import Config
from perspicacite.llm.embeddings import (
    LiteLLMEmbeddingProvider,
    SentenceTransformerEmbeddingProvider,
)
from perspicacite.pipeline import bibtex_kb

LOCAL_MODEL = "all-MiniLM-L6-v2"


def _write_bib(tmp_path: Path) -> Path:
    """A DOI-less article with an abstract.

    No DOI / URL / local file means the PDF-enrichment step touches no
    network, and the abstract guarantees at least one embeddable chunk.
    """
    bib = tmp_path / "refs.bib"
    bib.write_text(
        "@article{paper1,\n"
        "  title = {A Tiny Test Paper},\n"
        "  author = {Doe, Jane},\n"
        "  year = {2024},\n"
        "  abstract = {This abstract exists so the paper yields embeddable chunks.}\n"
        "}\n",
        encoding="utf-8",
    )
    return bib


def _config_with_local_model() -> Config:
    cfg = Config()
    cfg.knowledge_base.embedding_model = LOCAL_MODEL
    return cfg


async def test_create_kb_from_bibtex_uses_routing_factory_for_local_model(tmp_path):
    """The provider built inside create_kb_from_bibtex must be the routed
    SentenceTransformer provider — never a raw LiteLLM provider — for a
    local ``all-*`` model. Collaborators are faked so this runs fast and
    offline; only the real ``create_embedding_provider`` routing is exercised.
    """
    captured: dict[str, object] = {}

    class _FakeSessionStore:
        def __init__(self, db_path):
            self.db_path = db_path

        async def init_db(self):
            return None

        async def get_kb_metadata(self, name):
            return None  # KB does not exist yet → proceed to create it

        async def save_kb_metadata(self, kb):
            return None

    class _FakeVectorStore:
        def __init__(self, *, persist_dir, embedding_provider):
            captured["chroma_provider"] = embedding_provider

        async def create_collection(self, name):
            return None

        async def delete_collection(self, name):
            return None

    class _FakeDKB:
        def __init__(self, vector_store, embedding_provider, *, config):
            captured["dkb_provider"] = embedding_provider

        async def add_papers(self, papers, include_full_text=True):
            return 2  # pretend two chunks were embedded

    @asynccontextmanager
    async def _fake_client(**kwargs):
        yield object()

    async def _fake_enrich(papers, **kwargs):
        return {"attempted": 0, "success": 0, "failed": 0, "skipped_no_doi": len(papers)}

    cfg = _config_with_local_model()
    bib = _write_bib(tmp_path)

    with mock.patch(
        "perspicacite.memory.session_store.SessionStore", _FakeSessionStore
    ), mock.patch(
        "perspicacite.retrieval.chroma_store.ChromaVectorStore", _FakeVectorStore
    ), mock.patch.object(
        bibtex_kb, "DynamicKnowledgeBase", _FakeDKB
    ), mock.patch.object(
        bibtex_kb, "PDFParser", lambda *a, **k: object()
    ), mock.patch.object(
        bibtex_kb, "enrich_papers_with_pdf", _fake_enrich
    ), mock.patch(
        "perspicacite.pipeline.download.cookies.build_authenticated_client",
        _fake_client,
    ):
        result = await bibtex_kb.create_kb_from_bibtex(
            cfg,
            kb_name="local-embed-test",
            bib_path=bib,
            description=None,
            session_db=tmp_path / "data" / "test.db",
            chroma_dir=tmp_path / "chroma",
        )

    assert result["chunks_added"] == 2

    # The exact regression: a local model must NOT be wrapped in LiteLLM.
    for key in ("chroma_provider", "dkb_provider"):
        provider = captured[key]
        assert isinstance(provider, SentenceTransformerEmbeddingProvider), (
            f"{key} is {type(provider).__name__}; expected "
            "SentenceTransformerEmbeddingProvider. The call site likely "
            "reverted to LiteLLMEmbeddingProvider — use create_embedding_provider()."
        )
        assert not isinstance(provider, LiteLLMEmbeddingProvider)
        assert provider.model_name == LOCAL_MODEL


async def test_create_kb_from_bibtex_local_model_produces_chunks(tmp_path):
    """End-to-end: a KB built with a local SentenceTransformer embedding model
    produces chunks > 0. Skips if local embedding is unavailable in this env.
    """
    # Pre-flight: only assert chunks>0 when local embedding genuinely works,
    # so a missing model / offline env skips instead of falsely failing. With
    # the bug present, embedding works fine but the *call site* picks LiteLLM,
    # so the end-to-end count is what catches the regression.
    probe = SentenceTransformerEmbeddingProvider(model=LOCAL_MODEL, device="cpu")
    try:
        vecs = await probe.embed(["preflight probe sentence"])
    except Exception as exc:  # ImportError, model download failure, etc.
        pytest.skip(f"local sentence-transformers embedding unavailable: {exc}")
    assert vecs and len(vecs[0]) > 0

    cfg = _config_with_local_model()
    bib = _write_bib(tmp_path)

    result = await bibtex_kb.create_kb_from_bibtex(
        cfg,
        kb_name="local-embed-e2e",
        bib_path=bib,
        description=None,
        session_db=tmp_path / "data" / "test.db",
        chroma_dir=tmp_path / "chroma",
    )

    assert result["chunks_added"] > 0, (
        "KB built with a local embedding model produced 0 chunks — the ingest "
        "path likely hardcodes LiteLLMEmbeddingProvider instead of routing via "
        "create_embedding_provider()."
    )
