"""Unit tests for MCPProgressAdapter."""
import pytest
from unittest.mock import AsyncMock

from perspicacite.mcp.progress_adapter import MCPProgressAdapter


@pytest.mark.asyncio
async def test_query_rephrased_event():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({
        "kind": "query_rephrased",
        "original": "what is X",
        "rewritten": "X",
    })
    ctx.report_progress.assert_called_once()
    args = ctx.report_progress.call_args.kwargs
    assert "Rewrote search query" in args["message"]


@pytest.mark.asyncio
async def test_batch_progress_updates_counters():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({
        "kind": "batch_progress",
        "stage": "abstract_analysis",
        "current": 3, "total": 10,
    })
    args = ctx.report_progress.call_args.kwargs
    assert args["progress"] == 3
    assert args["total"] == 10
    assert "abstract_analysis: 3/10" in args["message"]


@pytest.mark.asyncio
async def test_throttling_drops_rapid_events(monkeypatch):
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    # First event passes through.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "a", "rewritten": "b",
    })
    # Second event same instant — must be dropped.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "c", "rewritten": "d",
    })
    assert ctx.report_progress.call_count == 1


@pytest.mark.asyncio
async def test_unknown_kind_silently_ignored():
    ctx = AsyncMock()
    adapter = MCPProgressAdapter(ctx)
    await adapter.on_event({"kind": "not_a_real_event"})
    ctx.report_progress.assert_not_called()


@pytest.mark.asyncio
async def test_ctx_error_swallowed():
    ctx = AsyncMock()
    ctx.report_progress.side_effect = RuntimeError("transport down")
    adapter = MCPProgressAdapter(ctx)
    # Must not raise.
    await adapter.on_event({
        "kind": "query_rephrased", "original": "a", "rewritten": "b",
    })
