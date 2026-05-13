"""Tests for per-request contextvar isolation in AgenticRAGMode."""

import asyncio

import pytest

from perspicacite.rag.agentic.context import (
    agentic_request_overrides,
    get_current_recency_weight,
)


@pytest.mark.asyncio
async def test_two_concurrent_overrides_do_not_collide():
    """Each task's contextvar sees its own value, not the other's."""
    results = {}

    async def task(name, weight):
        with agentic_request_overrides(
            recency_weight=weight,
            recency_half_life_years=None,
            kb_metas=None,
        ):
            await asyncio.sleep(0)
            results[name] = get_current_recency_weight()
            await asyncio.sleep(0)
            # Re-read after a context switch — should still be the same
            assert get_current_recency_weight() == weight

    await asyncio.gather(task("a", 0.5), task("b", 0.9))
    assert results == {"a": 0.5, "b": 0.9}
