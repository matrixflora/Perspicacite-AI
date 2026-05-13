"""Per-request contextvar overrides for AgenticOrchestrator.

Using ContextVars instead of mutating the shared singleton orchestrator ensures
that two concurrent agentic requests each see their own recency/kb_metas settings
without racing on shared mutable state.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_current_recency_weight: ContextVar[float | None] = ContextVar(
    "agentic_recency_weight", default=None
)
_current_recency_half_life: ContextVar[float | None] = ContextVar(
    "agentic_recency_half_life", default=None
)
_current_kb_metas: ContextVar[list[Any] | None] = ContextVar(
    "agentic_kb_metas", default=None
)


@contextmanager
def agentic_request_overrides(
    *,
    recency_weight: float | None,
    recency_half_life_years: float | None,
    kb_metas: list[Any] | None,
):
    """Context manager that installs per-request overrides for the duration of a block.

    Each asyncio Task gets its own copy of the ContextVar values, so concurrent
    requests never see each other's settings.
    """
    t1 = _current_recency_weight.set(recency_weight)
    t2 = _current_recency_half_life.set(recency_half_life_years)
    t3 = _current_kb_metas.set(kb_metas)
    try:
        yield
    finally:
        _current_recency_weight.reset(t1)
        _current_recency_half_life.reset(t2)
        _current_kb_metas.reset(t3)


def get_current_recency_weight() -> float | None:
    """Return the per-request recency weight, or None if not set."""
    return _current_recency_weight.get()


def get_current_recency_half_life() -> float | None:
    """Return the per-request recency half-life (years), or None if not set."""
    return _current_recency_half_life.get()


def get_current_kb_metas() -> list[Any] | None:
    """Return the per-request kb_metas list, or None if not set."""
    return _current_kb_metas.get()
