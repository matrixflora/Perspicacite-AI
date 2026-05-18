"""Maps internal RAG telemetry events to MCP ``ctx.report_progress`` calls.

Background: the SSE chat router consumes a rich vocabulary of event
``kind``s — ``query_rephrased``, ``provider_progress``, ``batch_progress``,
``source``, ``status``. MCP's protocol only supports
``(progress: int, total: int, message: str)`` notifications. This adapter
collapses the rich events into human-readable progress messages while
preserving the cumulative progress counter so MCP clients see a sensible
0 → 100% bar.

Throttling: progress notifications are rate-limited to ≥ 1 second
spacing to avoid spamming slow clients (per spec Risks & Mitigations).
"""
from __future__ import annotations

import time
from typing import Any


class MCPProgressAdapter:
    """Forwards RAG telemetry events to ``ctx.report_progress``."""

    _MIN_SPACING_S = 1.0

    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self._last_emit_t = 0.0
        # Running counters — best-effort estimate of progress
        self._progress = 0
        self._total = 100  # default scale until a batch_progress event reveals real total

    async def on_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        msg = None
        if kind == "query_rephrased":
            orig = event.get("original", "")
            rew = event.get("rewritten", "")
            msg = f"Rewrote search query: '{orig}' → '{rew}'"
        elif kind == "provider_progress" and event.get("phase") == "start":
            provs = ", ".join(event.get("providers", []) or [])
            msg = f"Querying databases: {provs}"
        elif kind == "provider_progress" and event.get("phase") == "done":
            by = event.get("by_provider", {}) or {}
            counts = ", ".join(
                f"{k}: {v}" for k, v in sorted(by.items(), key=lambda kv: -kv[1])
            )
            total = event.get("total", 0)
            msg = (
                f"Database results — total {total} hits"
                + (f" ({counts})" if counts else "")
            )
        elif kind == "batch_progress":
            cur = int(event.get("current", 0))
            tot = int(event.get("total", 0)) or 1
            stage = event.get("stage", "batch")
            self._progress = cur
            self._total = tot
            msg = f"{stage}: {cur}/{tot}"
        elif kind == "rate_limit_low":
            provider = event.get("provider", "?")
            remaining = event.get("remaining", "?")
            msg = f"Rate limit low for {provider}: {remaining} reqs remaining"

        if msg is None:
            return

        # Throttle: do not fire notifications more than once per second.
        now = time.monotonic()
        if now - self._last_emit_t < self._MIN_SPACING_S:
            return
        self._last_emit_t = now

        try:
            await self.ctx.report_progress(
                progress=self._progress,
                total=self._total,
                message=msg,
            )
        except Exception:
            # Never let MCP transport hiccups break the RAG pipeline.
            return
