"""Regression: _make_or_get_kb must read app_state.embedding_provider.

Both AppState and MCPState expose `embedding_provider` (there is no
`embedding_service` attribute). Reading the wrong name broke ingest_asb_run
on every path with "'MCPState' object has no attribute 'embedding_service'".
"""
from __future__ import annotations

import asyncio
from unittest import mock

import perspicacite.pipeline.asb.run_ingest as ri


class _FakeState:
    """Mirrors the relevant AppState/MCPState surface: vector_store +
    embedding_provider, and DELIBERATELY no embedding_service."""

    def __init__(self) -> None:
        self.vector_store = object()
        self.embedding_provider = "PROVIDER_SENTINEL"


def test_make_or_get_kb_uses_embedding_provider() -> None:
    captured: dict = {}

    class _FakeDKB:
        def __init__(self, *, vector_store, embedding_service, config):
            captured["embedding_service"] = embedding_service
            # Mimic DKB.__init__: default to a session-suffixed collection,
            # which _make_or_get_kb MUST override to the canonical name.
            self.collection_name = "session_suffixed_WRONG"
            self.name = None
            self.description = None

    async def _fake_create(app_state, name, description):
        return ({"name": name}, True)

    with mock.patch(
        "perspicacite.pipeline.search_to_kb._create_kb_if_missing",
        new=_fake_create,
    ), mock.patch(
        "perspicacite.rag.dynamic_kb.DynamicKnowledgeBase", _FakeDKB
    ), mock.patch(
        "perspicacite.models.kb.chroma_collection_name_for_kb",
        lambda name: f"kb_{name}",
    ):
        kb = asyncio.run(
            ri._make_or_get_kb("asb-skills", description="d", app_state=_FakeState())
        )

    # The provider (not a non-existent embedding_service) flows into the KB.
    assert captured["embedding_service"] == "PROVIDER_SENTINEL"
    assert kb.name == "asb-skills"
    # Writes must target the CANONICAL collection search/stats read, not the
    # session-suffixed default — else ingested data is invisible.
    assert kb.collection_name == "kb_asb-skills"


def test_make_or_get_kb_would_fail_on_missing_attr() -> None:
    """Guard the precise regression: a state lacking embedding_provider must
    raise AttributeError (not silently pass), so the wiring stays honest."""
    class _Bare:
        vector_store = object()  # no embedding_provider

    async def _fake_create(app_state, name, description):
        return ({"name": name}, True)

    with mock.patch(
        "perspicacite.pipeline.search_to_kb._create_kb_if_missing",
        new=_fake_create,
    ), mock.patch(
        "perspicacite.models.kb.chroma_collection_name_for_kb",
        lambda name: f"col_{name}",
    ):
        try:
            asyncio.run(ri._make_or_get_kb("x", app_state=_Bare()))
        except AttributeError:
            return
    raise AssertionError("expected AttributeError on a state without embedding_provider")
