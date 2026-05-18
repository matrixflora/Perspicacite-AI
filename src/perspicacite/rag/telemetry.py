"""TelemetrySink: unified protocol for in-RAG-pipeline progress events.

Three implementations:

- ``ListTelemetrySink``     : drop-in replacement for the legacy
  ``telemetry: list[dict]`` pattern; the SSE chat router and existing
  call sites continue to use this.

- ``CallbackTelemetrySink`` : invokes an awaitable on each event.
  The MCP layer wraps ``ctx.report_progress`` in this so external
  agents see live progress notifications during long-running tools.

- ``NullTelemetrySink``     : drops every event. Useful in tests /
  batch scripts that don't care about progress notifications.

Both `append` (sync, list-style) and `on_event_async` (async, callback-style)
APIs are exposed on every sink so mode code can use whichever feels
natural without conditionally checking the sink type.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Protocol


class TelemetrySink(Protocol):
    """Any object usable as the ``telemetry`` parameter on RAG helpers."""

    def append(self, event: dict[str, Any]) -> None: ...
    async def on_event_async(self, event: dict[str, Any]) -> None: ...


class ListTelemetrySink:
    """Stores events in a plain list; drain after the await.

    Preserves the legacy semantics. Existing code that does
    ``telemetry.append({...})`` keeps working.
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    async def on_event_async(self, event: dict[str, Any]) -> None:
        self.events.append(event)

    def __iter__(self):
        return iter(self.events)

    def __len__(self) -> int:
        return len(self.events)

    def __bool__(self) -> bool:
        return bool(self.events)


class CallbackTelemetrySink:
    """Invokes ``callback(event)`` (awaitable) on every event.

    Used by the MCP progress adapter. Provides ``append`` as a sync
    fire-and-forget shim that schedules the callback on the running
    loop; prefer ``on_event_async`` from async contexts.
    """

    def __init__(
        self, callback: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self._callback = callback
        # Mirror events into a buffer for diagnostics.
        self.events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._callback(event))
        except RuntimeError:
            # No running loop — caller is sync; drop event silently
            # (the legacy SSE drain path uses .events directly).
            pass

    async def on_event_async(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        try:
            await self._callback(event)
        except Exception:
            pass  # never let telemetry errors break the RAG pipeline


class NullTelemetrySink:
    """Drops every event. Useful in tests / batch scripts."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def append(self, event: dict[str, Any]) -> None:
        return None

    async def on_event_async(self, event: dict[str, Any]) -> None:
        return None

    def __iter__(self):
        return iter([])

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False
