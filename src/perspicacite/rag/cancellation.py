"""Process-wide cancellation registry for long-running RAG tasks.

Backs both the SSE chat router (``/api/chat/cancel``) and the MCP
``cancel_task`` tool. Replaces the old ``_CANCELLED_CHAT_IDS: set[str]``
which had no garbage collection. Internally a dict mapping task-id ->
cancellation timestamp; the dict is pruned by TTL + size cap so memory
stays bounded under load.
"""
from __future__ import annotations

import asyncio
import time

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.rag.cancellation")

# Tunables. _TTL_SECONDS protects against ID leaks; _MAX_ENTRIES caps
# memory under DoS-like cancellation floods.
_TTL_SECONDS: float = 3600.0  # 1 hour
_MAX_ENTRIES: int = 1000

_lock = asyncio.Lock()
_cancelled: dict[str, float] = {}


def _prune_locked(now: float | None = None) -> None:
    """Caller must hold ``_lock``. Drops TTL-expired entries, then trims
    to ``_MAX_ENTRIES`` by oldest-first."""
    now = now if now is not None else time.monotonic()
    expired = [k for k, ts in _cancelled.items() if now - ts > _TTL_SECONDS]
    for k in expired:
        del _cancelled[k]
    if len(_cancelled) > _MAX_ENTRIES:
        ordered = sorted(_cancelled.items(), key=lambda kv: kv[1])
        for k, _ in ordered[: len(_cancelled) - _MAX_ENTRIES]:
            del _cancelled[k]


async def mark_cancelled(task_id: str) -> None:
    """Mark ``task_id`` as cancelled. Idempotent.

    Updates the timestamp on repeat calls so the entry stays warm in
    the TTL window. Returns when the registry is updated; callers may
    use this for both chat-conversation IDs and MCP task IDs.
    """
    if not task_id:
        return
    async with _lock:
        _cancelled[task_id] = time.monotonic()
        _prune_locked()
    logger.info("cancel_registered", task_id=task_id, size=len(_cancelled))


def is_cancelled(task_id: str | None) -> bool:
    """Cheap synchronous check. Returns False for None / empty IDs.

    Intentionally not locked: dict reads are atomic in CPython and
    occasional staleness is harmless (caller will check again on the
    next iteration). Hot path inside RAG cycles.
    """
    if not task_id:
        return False
    return task_id in _cancelled


async def clear(task_id: str) -> None:
    """Remove a task from the registry. Use after task cleanup completes."""
    if not task_id:
        return
    async with _lock:
        _cancelled.pop(task_id, None)


async def snapshot() -> dict[str, float]:
    """Return a snapshot of the registry — used by tests and diagnostics."""
    async with _lock:
        return dict(_cancelled)


async def reset_for_tests() -> None:
    """Test-only helper to start each test from an empty registry."""
    async with _lock:
        _cancelled.clear()
