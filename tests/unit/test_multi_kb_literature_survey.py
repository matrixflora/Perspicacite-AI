"""literature_survey accepts kb_names without erroring.

Task 4 (Cycle 3, Phase 1) of the multi-KB plan. literature_survey doesn't
retrieve from a KB — it runs an external SciLEx search — but the API contract
must still accept ``request.kb_names`` and select the correct KB for any
storage-targeting (provenance traces, future paper persistence).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.rag.modes.literature_survey import (
    LiteratureSurveyRAGMode,
    _target_kb,
)


class _FakeLLM:
    """Minimal async LLM stub — survey doesn't reach LLM if _broad_search empty."""

    async def complete(self, **_):
        return SimpleNamespace(content="...")

    async def complete_stream(self, **_):
        async def gen():
            yield SimpleNamespace(content="...")

        return gen()


def _make_request(**overrides) -> RAGRequest:
    base = {
        "query": "microbiome",
        "mode": RAGMode.LITERATURE_SURVEY,
        "kb_name": "k1",
    }
    base.update(overrides)
    return RAGRequest(**base)


def test_target_kb_helper_prefers_kb_names_first():
    """`_target_kb` returns kb_names[0] when set, else request.kb_name."""
    r1 = _make_request(kb_names=["alpha", "beta", "gamma"])
    assert _target_kb(r1) == "alpha"

    r2 = _make_request(kb_name="solo")  # no kb_names
    assert _target_kb(r2) == "solo"

    r3 = _make_request(kb_name="fallback", kb_names=None)
    assert _target_kb(r3) == "fallback"

    r4 = _make_request(kb_name="fallback", kb_names=[])
    assert _target_kb(r4) == "fallback"


@pytest.mark.asyncio
async def test_literature_survey_stream_accepts_kb_names_no_error(monkeypatch):
    """execute_stream with kb_names must not yield any 'error' events."""
    cfg = Config()
    mode = LiteratureSurveyRAGMode(cfg)
    request = _make_request(kb_names=["k1", "k2"])

    async def _empty_search(*_a, **_kw):
        return []

    monkeypatch.setattr(mode, "_broad_search", _empty_search, raising=False)

    events: list[StreamEvent] = []
    async for ev in mode.execute_stream(
        request,
        _FakeLLM(),
        vector_store=None,
        embedding_provider=None,
        tools=None,
    ):
        events.append(ev)

    error_events = [e for e in events if getattr(e, "event", "") == "error"]
    assert not error_events, f"Unexpected error events: {error_events}"


@pytest.mark.asyncio
async def test_literature_survey_execute_accepts_kb_names_no_error(monkeypatch):
    """The non-streaming execute path also tolerates kb_names."""
    cfg = Config()
    mode = LiteratureSurveyRAGMode(cfg)
    request = _make_request(kb_names=["k1", "k2"])

    async def _empty_search(*_a, **_kw):
        return []

    monkeypatch.setattr(mode, "_broad_search", _empty_search, raising=False)

    response = await mode.execute(
        request,
        _FakeLLM(),
        vector_store=None,
        embedding_provider=None,
        tools=None,
    )
    # Empty-search path returns a "no papers found" response, not an exception.
    assert response.mode == RAGMode.LITERATURE_SURVEY
    assert response.sources == []


@pytest.mark.asyncio
async def test_literature_survey_logs_multi_kb_storage_warning(monkeypatch):
    """When kb_names has >1 entry, a `survey_kb_context_prepared` info log fires
    listing all provided kb_names.

    The old `survey_multi_kb_storage` event was replaced in Task 4 by the
    _prepare_kb_context() call which emits `survey_kb_context_prepared`.

    We patch the module-level structlog logger to capture calls directly,
    which is more robust than caplog (structlog isn't routed through stdlib
    logging in the unit-test environment).
    """
    cfg = Config()
    mode = LiteratureSurveyRAGMode(cfg)
    request = _make_request(kb_names=["primary", "secondary", "tertiary"])

    async def _empty_search(*_a, **_kw):
        return []

    monkeypatch.setattr(mode, "_broad_search", _empty_search, raising=False)

    captured: list[tuple[str, dict]] = []

    from perspicacite.rag.modes import literature_survey as ls_mod

    real_info = ls_mod.logger.info

    def _capture_info(event, **kw):
        captured.append((event, kw))
        return real_info(event, **kw)

    monkeypatch.setattr(ls_mod.logger, "info", _capture_info, raising=False)

    async for _ in mode.execute_stream(
        request,
        _FakeLLM(),
        vector_store=None,
        embedding_provider=None,
        tools=None,
    ):
        pass

    matching = [(ev, kw) for ev, kw in captured if ev == "survey_kb_context_prepared"]
    assert matching, f"survey_kb_context_prepared not logged. Got: {[e for e,_ in captured]}"
    _, kw = matching[0]
    assert set(kw["kb_names"]) == {"primary", "secondary", "tertiary"}


@pytest.mark.asyncio
async def test_literature_survey_single_kb_no_multi_log(monkeypatch):
    """Single-KB requests do NOT emit the multi-KB storage log."""
    cfg = Config()
    mode = LiteratureSurveyRAGMode(cfg)
    request = _make_request(kb_name="solo")  # no kb_names

    async def _empty_search(*_a, **_kw):
        return []

    monkeypatch.setattr(mode, "_broad_search", _empty_search, raising=False)

    captured: list[str] = []

    from perspicacite.rag.modes import literature_survey as ls_mod

    real_info = ls_mod.logger.info

    def _capture_info(event, **kw):
        captured.append(event)
        return real_info(event, **kw)

    monkeypatch.setattr(ls_mod.logger, "info", _capture_info, raising=False)

    async for _ in mode.execute_stream(
        request,
        _FakeLLM(),
        vector_store=None,
        embedding_provider=None,
        tools=None,
    ):
        pass

    assert "survey_multi_kb_storage" not in captured
