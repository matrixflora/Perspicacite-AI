"""Unit tests for TelemetrySink implementations."""
import asyncio
import pytest

from perspicacite.rag.telemetry import (
    ListTelemetrySink, CallbackTelemetrySink, NullTelemetrySink,
)


def test_list_sink_append_and_iterate():
    s = ListTelemetrySink()
    s.append({"a": 1})
    s.append({"b": 2})
    assert list(s) == [{"a": 1}, {"b": 2}]
    assert len(s) == 2
    assert bool(s) is True


def test_list_sink_empty_is_falsey():
    s = ListTelemetrySink()
    assert bool(s) is False
    assert len(s) == 0


@pytest.mark.asyncio
async def test_callback_sink_invokes_callback():
    received: list[dict] = []

    async def cb(e):
        received.append(e)

    s = CallbackTelemetrySink(cb)
    await s.on_event_async({"x": 1})
    await s.on_event_async({"y": 2})
    assert received == [{"x": 1}, {"y": 2}]


@pytest.mark.asyncio
async def test_callback_sink_buffers_events():
    """The .events list lets diagnostics read what was emitted."""
    async def cb(_e):
        return

    s = CallbackTelemetrySink(cb)
    await s.on_event_async({"a": 1})
    assert s.events == [{"a": 1}]


@pytest.mark.asyncio
async def test_callback_sink_swallows_callback_errors():
    """Callback exceptions must not break the RAG pipeline."""
    async def bad(e):
        raise RuntimeError("boom")

    s = CallbackTelemetrySink(bad)
    await s.on_event_async({"x": 1})  # must not raise
    assert s.events == [{"x": 1}]


def test_null_sink_drops_everything():
    s = NullTelemetrySink()
    s.append({"x": 1})
    assert len(s) == 0
    assert bool(s) is False
