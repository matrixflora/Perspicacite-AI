"""Unit tests for the embedding-model compatibility check at KB-ingest time.

Companion module: :mod:`perspicacite.rag.kb_compat`. The check is invoked
by all three KB-creation sites (search_to_kb._create_kb_if_missing,
asb/run_ingest._make_or_get_kb, github_kb._add_papers_to_kb) right
after ``get_kb_metadata`` resolves an existing KB. Until 2026-05-16 the
sites silently re-ingested with a different model than the existing
KB was built with — see backlog entry in
``docs/superpowers/handoffs/2026-05-16-backlog-session-handoff.md``.
"""
from __future__ import annotations

from typing import Any

import pytest

from perspicacite.rag.kb_compat import (
    EmbeddingModelConflictError,
    check_embedding_compat_for_ingest,
)


class _FakeKBMeta:
    """Minimal stand-in for the KnowledgeBase pydantic model — only the
    two attributes the check reads are exposed."""

    def __init__(self, name: str, embedding_model: str | None) -> None:
        self.name = name
        self.embedding_model = embedding_model


class _FakeEmbedder:
    """Minimal stand-in for the embedding service — exposes ``model_name``."""

    def __init__(self, model_name: str | None) -> None:
        self.model_name = model_name


def test_no_op_when_kb_meta_is_none() -> None:
    """No-op when the KB doesn't exist yet — caller will create it."""
    # Must not raise and must not poke at the embedder.
    check_embedding_compat_for_ingest(
        kb_meta=None,
        embedding_service=_FakeEmbedder("any-model"),
    )


def test_no_op_when_models_match() -> None:
    """Matching embedding-model names → no raise."""
    check_embedding_compat_for_ingest(
        kb_meta=_FakeKBMeta(name="kb-a", embedding_model="model-A"),
        embedding_service=_FakeEmbedder("model-A"),
    )


def test_raises_when_models_differ() -> None:
    """Conflicting embedding-model names → EmbeddingModelConflictError."""
    with pytest.raises(EmbeddingModelConflictError):
        check_embedding_compat_for_ingest(
            kb_meta=_FakeKBMeta(name="kb-a", embedding_model="model-A"),
            embedding_service=_FakeEmbedder("model-B"),
        )


def test_exception_carries_all_3_fields() -> None:
    """kb_name, existing_model, and attempted_model are on the exception."""
    try:
        check_embedding_compat_for_ingest(
            kb_meta=_FakeKBMeta(name="my_kb", embedding_model="old-model"),
            embedding_service=_FakeEmbedder("new-model"),
        )
    except EmbeddingModelConflictError as exc:
        assert exc.kb_name == "my_kb"
        assert exc.existing_model == "old-model"
        assert exc.attempted_model == "new-model"
        # The repr should mention all three so operators can diagnose
        # without a debugger.
        msg = str(exc)
        assert "my_kb" in msg
        assert "old-model" in msg
        assert "new-model" in msg
    else:  # pragma: no cover — failure path
        raise AssertionError("expected EmbeddingModelConflictError")


def test_no_op_when_service_missing_model_name() -> None:
    """Best-effort: a mock embedder without ``.model_name`` doesn't crash."""
    # spec=[] removes auto-attribute behaviour. Then setattr=delattr to
    # ensure ``model_name`` truly isn't there. Simpler: use a bare object.
    class _NoModelName:
        pass

    # Pre-existing KB metadata exists but the service can't tell us its model
    # — skip the check rather than fail.
    check_embedding_compat_for_ingest(
        kb_meta=_FakeKBMeta(name="kb-a", embedding_model="model-A"),
        embedding_service=_NoModelName(),
    )


def test_no_op_when_kb_missing_embedding_model() -> None:
    """Best-effort: kb_meta with ``embedding_model=None`` doesn't crash."""
    check_embedding_compat_for_ingest(
        kb_meta=_FakeKBMeta(name="kb-a", embedding_model=None),
        embedding_service=_FakeEmbedder("model-A"),
    )


def test_no_op_when_service_model_name_is_none() -> None:
    """Best-effort: embedder reports ``model_name=None`` (e.g. test stub) → skip."""
    check_embedding_compat_for_ingest(
        kb_meta=_FakeKBMeta(name="kb-a", embedding_model="model-A"),
        embedding_service=_FakeEmbedder(None),
    )


def test_exception_is_a_value_error_subclass() -> None:
    """Callers may still ``except ValueError`` and catch this conflict —
    important for the existing search_to_kb error-handling discipline."""
    exc = EmbeddingModelConflictError(
        kb_name="x", existing_model="a", attempted_model="b",
    )
    assert isinstance(exc, ValueError)


# ---------------------------------------------------------------------------
# Direct coverage for ``_create_kb_if_missing`` (Site A)
# ---------------------------------------------------------------------------


class _StubSessionStore:
    """In-memory session-store stub exposing the two coroutines
    ``_create_kb_if_missing`` consumes."""

    def __init__(self, existing_meta: Any = None) -> None:
        self.existing_meta = existing_meta
        self.saved: list[Any] = []

    async def get_kb_metadata(self, _name: str) -> Any:
        return self.existing_meta

    async def save_kb_metadata(self, kb: Any) -> None:
        self.saved.append(kb)


class _StubVectorStore:
    def __init__(self) -> None:
        self.created: list[str] = []

    async def create_collection(self, name: str) -> None:
        self.created.append(name)


class _StubConfig:
    class _KBSub:
        chunk_size = 256
        chunk_overlap = 32

    knowledge_base = _KBSub()


class _StubAppState:
    def __init__(self, *, existing_meta: Any, model_name: str) -> None:
        self.session_store = _StubSessionStore(existing_meta=existing_meta)
        self.vector_store = _StubVectorStore()
        self.embedding_provider = _FakeEmbedder(model_name)
        self.config = _StubConfig()


@pytest.mark.asyncio
async def test_create_kb_if_missing_raises_on_embedding_conflict() -> None:
    """Site A: pre-existing KB built with a different model → raise."""
    from perspicacite.pipeline.search_to_kb import _create_kb_if_missing

    existing = _FakeKBMeta(name="kb_a", embedding_model="old-model")
    app_state = _StubAppState(existing_meta=existing, model_name="new-model")

    with pytest.raises(EmbeddingModelConflictError) as excinfo:
        await _create_kb_if_missing(app_state, "kb_a", "desc")

    assert excinfo.value.kb_name == "kb_a"
    assert excinfo.value.existing_model == "old-model"
    assert excinfo.value.attempted_model == "new-model"
    # No partial-state writes when the conflict trips.
    assert app_state.vector_store.created == []
    assert app_state.session_store.saved == []


@pytest.mark.asyncio
async def test_create_kb_if_missing_reuses_existing_when_models_match() -> None:
    """Site A happy path: same model → reuse, no create, no save."""
    from perspicacite.pipeline.search_to_kb import _create_kb_if_missing

    existing = _FakeKBMeta(name="kb_a", embedding_model="same-model")
    app_state = _StubAppState(existing_meta=existing, model_name="same-model")

    kb_meta, created = await _create_kb_if_missing(app_state, "kb_a", "desc")
    assert kb_meta is existing
    assert created is False
    assert app_state.vector_store.created == []
    assert app_state.session_store.saved == []
