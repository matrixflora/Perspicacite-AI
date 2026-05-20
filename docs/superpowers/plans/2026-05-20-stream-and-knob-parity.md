# Stream parity & knob parity — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.
>
> **Plan/spec files in `docs/superpowers/` are NOT to be committed** (user request).

**Goal:** Extend the MCP `MCPProgressAdapter` to forward three additional event kinds (`phase_progress`, `tokens`, `cost_estimate`) and add four new optional knobs to `generate_report` (`screen_method`, `screen_threshold`, `max_papers_to_download`, `databases`) and one to `search_literature` (`databases`). Final-response JSON gains optional `attempts`, `query_rephrasings`, and `usage` metadata when present.

**Architecture:** Server-only changes. The progress adapter at `src/perspicacite/mcp/progress_adapter.py` learns new event kinds and appends a `\nMETA:<json>` tail to the message so structured-data consumers can parse. The two MCP tools (`generate_report`, `search_literature`) gain optional kwargs that flow through into `RAGRequest`. Telemetry-emitting modes in `rag/modes/*.py` and the SciLEx adapter learn to honor the knobs. No consumer-side changes (ASB, Scriptorium, audit continue to work).

**Tech Stack:** Python 3.12+, FastMCP, pytest, uv. Same as C+D sprint.

**Spec:** `docs/superpowers/specs/2026-05-20-stream-and-knob-parity-design.md`.

---

## File map

- Modify: `src/perspicacite/mcp/progress_adapter.py` (add 3 event-kind branches, META tail serializer)
- Modify: `src/perspicacite/mcp/server.py` — `generate_report` (~line 1231) and `search_literature` (~line 348) signatures + kwarg pass-through
- Modify: `src/perspicacite/rag/engine.py` — `RAGRequest` model gains the new fields
- Modify: `src/perspicacite/rag/modes/*.py` — read `screen_method`, `screen_threshold`, `max_papers_to_download` where applicable (advanced.py, agentic, profound, literature_survey)
- Modify: `src/perspicacite/search/scilex_adapter.py` or `pipeline/external/*` — filter providers by `databases`
- Modify: `src/perspicacite/rag/telemetry.py` (or wherever events originate) to emit `phase_progress`, `tokens`, `cost_estimate` events
- Extend: `tests/unit/test_mcp_progress_adapter.py` (extend with new event types)
- Create: `tests/unit/test_mcp_generate_report_knobs.py`
- Create: `tests/unit/test_mcp_search_literature_knobs.py`

---

## Task 1: Progress adapter — accept new event kinds

**Files:**
- Modify: `src/perspicacite/mcp/progress_adapter.py`
- Modify: `tests/unit/test_mcp_progress_adapter.py`

The adapter currently handles `query_rephrased`, `provider_progress`, `batch_progress`, `rate_limit_low`. We add `phase_progress`, `tokens`, `cost_estimate`. Each new branch produces a human-readable message AND appends a `\nMETA:<json>` tail so callers can parse structured data.

- [ ] **Step 1: Read existing tests**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
cat tests/unit/test_mcp_progress_adapter.py
```

Note the existing test style — they construct a fake ctx with an `async report_progress` method and assert on the calls list.

- [ ] **Step 2: Add failing tests for the three new event kinds**

Append to `tests/unit/test_mcp_progress_adapter.py`:

```python
"""Coverage extension for phase_progress / tokens / cost_estimate events."""
import json

import pytest

from perspicacite.mcp.progress_adapter import MCPProgressAdapter


class _Ctx:
    def __init__(self):
        self.calls = []

    async def report_progress(self, *, progress, total, message):
        self.calls.append((progress, total, message))


async def _wait_throttle(adapter):
    """Force the adapter to bypass its 1-second min-spacing for tests."""
    adapter._last_emit_t = 0.0


async def test_phase_progress_emits_human_and_meta():
    ctx = _Ctx()
    adapter = MCPProgressAdapter(ctx)
    await _wait_throttle(adapter)

    await adapter.on_event(
        {"kind": "phase_progress", "phase": "retrieve", "state": "running"}
    )

    assert len(ctx.calls) == 1
    _, _, msg = ctx.calls[0]
    assert "retrieve" in msg.lower()
    assert "running" in msg.lower()
    # META JSON tail for structured-data consumers
    assert "\nMETA:" in msg
    meta = json.loads(msg.split("\nMETA:", 1)[1])
    assert meta == {"kind": "phase_progress", "phase": "retrieve", "state": "running"}


async def test_tokens_emits_running_totals():
    ctx = _Ctx()
    adapter = MCPProgressAdapter(ctx)
    await _wait_throttle(adapter)

    await adapter.on_event(
        {
            "kind": "tokens",
            "in": 1200,
            "out": 350,
            "cumulative_in": 5400,
            "cumulative_out": 1100,
        }
    )

    assert len(ctx.calls) == 1
    _, _, msg = ctx.calls[0]
    assert "tokens" in msg.lower()
    meta = json.loads(msg.split("\nMETA:", 1)[1])
    assert meta["in"] == 1200
    assert meta["cumulative_out"] == 1100


async def test_cost_estimate_emits_usd():
    ctx = _Ctx()
    adapter = MCPProgressAdapter(ctx)
    await _wait_throttle(adapter)

    await adapter.on_event(
        {
            "kind": "cost_estimate",
            "usd": 0.034,
            "model": "deepseek/deepseek-chat",
        }
    )

    assert len(ctx.calls) == 1
    _, _, msg = ctx.calls[0]
    assert "0.034" in msg or "$0.034" in msg
    meta = json.loads(msg.split("\nMETA:", 1)[1])
    assert meta["usd"] == 0.034
    assert meta["model"] == "deepseek/deepseek-chat"


async def test_unknown_event_kind_is_silent():
    ctx = _Ctx()
    adapter = MCPProgressAdapter(ctx)
    await _wait_throttle(adapter)

    await adapter.on_event({"kind": "this_is_not_a_real_event", "data": 42})
    assert ctx.calls == []
```

(pytest-asyncio is in `auto` mode — no decorators.)

- [ ] **Step 3: Run and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_progress_adapter.py -v
```

Expected: 4 new tests fail.

- [ ] **Step 4: Implement the three new branches + META serializer**

In `src/perspicacite/mcp/progress_adapter.py`, add at the top:

```python
import json
```

In `on_event`, after the existing `rate_limit_low` branch and BEFORE the `if msg is None: return` line, add:

```python
        elif kind == "phase_progress":
            phase = event.get("phase", "?")
            state = event.get("state", "?")
            msg = f"Phase {phase}: {state}"
        elif kind == "tokens":
            inp = int(event.get("in", 0))
            out = int(event.get("out", 0))
            cum_in = int(event.get("cumulative_in", 0))
            cum_out = int(event.get("cumulative_out", 0))
            msg = (
                f"Tokens this turn: in={inp} out={out}; "
                f"cumulative: in={cum_in} out={cum_out}"
            )
        elif kind == "cost_estimate":
            usd = float(event.get("usd", 0.0))
            model = event.get("model", "?")
            msg = f"Cost estimate ${usd:.4f} ({model})"
```

Then, at the very end of the function (just before the `try: await self.ctx.report_progress(...)` block), build the META tail for the new kinds:

```python
        if kind in {"phase_progress", "tokens", "cost_estimate",
                    "query_rephrased", "provider_progress"}:
            try:
                meta_json = json.dumps(event, default=str)
                msg = f"{msg}\nMETA:{meta_json}"
            except (TypeError, ValueError):
                pass  # never let META serialization break the event
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_mcp_progress_adapter.py -v
```

Expected: all green (existing + 4 new).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/progress_adapter.py tests/unit/test_mcp_progress_adapter.py
git commit -m "feat(mcp): progress adapter handles phase_progress/tokens/cost_estimate + META JSON tail"
```

---

## Task 2: `search_literature` — `databases` kwarg

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (signature + plumbing of `search_literature`, ~line 348)
- Modify: `src/perspicacite/search/scilex_adapter.py` (filter providers by allowed list)
- Test: `tests/unit/test_mcp_search_literature_knobs.py` (new)

- [ ] **Step 1: Read current `search_literature` signature and SciLEx call site**

```bash
sed -n '347,500p' /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/mcp/server.py
grep -n "scilex_adapter\|SciLExAdapter\|search_with_warnings" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/mcp/server.py | head -5
```

Note: how it invokes SciLEx, which providers it queries by default, and where the `databases` argument should restrict the call.

- [ ] **Step 2: Write failing test** at `tests/unit/test_mcp_search_literature_knobs.py`:

```python
"""Tests for the new ``databases`` kwarg on search_literature MCP tool."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


def _state():
    s = MagicMock()
    s.session_store = MagicMock()
    s.config = MagicMock()
    return s


async def test_databases_kwarg_restricts_providers():
    captured = {}

    class _FakeSciLEx:
        async def search_with_warnings(self, query, *, databases=None, **kw):
            captured["databases"] = list(databases) if databases else None
            return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter",
        return_value=_FakeSciLEx(),
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5, databases=["arxiv", "crossref"]
        )

    assert captured["databases"] == ["arxiv", "crossref"]
    payload = json.loads(raw)
    assert payload["success"] is True


async def test_databases_unknown_entries_dropped_with_warning():
    captured = {}

    class _FakeSciLEx:
        async def search_with_warnings(self, query, *, databases=None, **kw):
            captured["databases"] = list(databases) if databases else None
            return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter",
        return_value=_FakeSciLEx(),
    ):
        raw = await mcp_server.search_literature(
            query="x", max_results=5,
            databases=["arxiv", "nonsense_db", "pubmed"],
        )

    # Unknown is silently dropped; valid ones flow through.
    assert "arxiv" in captured["databases"]
    assert "pubmed" in captured["databases"]
    assert "nonsense_db" not in captured["databases"]


async def test_databases_default_passes_none():
    captured = {"db": "sentinel"}

    class _FakeSciLEx:
        async def search_with_warnings(self, query, *, databases=None, **kw):
            captured["db"] = databases
            return [], {"errors_by_database": {}}

    with patch.object(mcp_server, "_require_state", return_value=_state()), patch(
        "perspicacite.search.scilex_adapter.SciLExAdapter",
        return_value=_FakeSciLEx(),
    ):
        await mcp_server.search_literature(query="x", max_results=5)

    assert captured["db"] is None
```

- [ ] **Step 3: Run and confirm failure**

```bash
uv run pytest tests/unit/test_mcp_search_literature_knobs.py -v
```

- [ ] **Step 4: Read the SciLEx adapter — does it already accept `databases`?**

```bash
grep -n "def search_with_warnings\|databases" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/search/scilex_adapter.py | head -10
```

Two cases:

**Case A — adapter already accepts `databases`:** Only `server.py` needs a thin pass-through with a filter step (drop unknown entries against a known-good set; if list becomes empty, pass None to fall back to defaults).

**Case B — adapter doesn't accept `databases`:** Add the parameter to `SciLExAdapter.search_with_warnings`. The adapter then uses the list to pick which `*_search` modules to invoke. Define a module-level `_KNOWN_DATABASES = {"arxiv","crossref","pubmed","semantic_scholar","openalex","europepmc","ads","pubchem","inspire","dblp","google_scholar"}` (use the set from the adapter's existing dispatch table — verify by reading).

- [ ] **Step 5: Implement in `server.py`**

Add `databases: list[str] | None = None` to the `search_literature` signature (~line 348 onward). Near the top of the function body, after `_require_state`:

```python
        from perspicacite.search.scilex_adapter import (
            SciLExAdapter, KNOWN_DATABASES,  # export this set from the adapter
        )

        filtered_databases: list[str] | None = None
        if databases is not None:
            filtered_databases = [d for d in databases if d in KNOWN_DATABASES]
            dropped = sorted(set(databases) - KNOWN_DATABASES)
            if dropped:
                logger.warning("mcp_search_literature_unknown_db", dropped=dropped)
            if not filtered_databases:
                filtered_databases = None  # fall back to defaults
```

Then pass `databases=filtered_databases` through to the SciLEx call. Match the call site you found in Step 1.

- [ ] **Step 6: Run tests + full suite**

```bash
uv run pytest tests/unit/test_mcp_search_literature_knobs.py -v
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -10
```

Expected: 3 new pass, no regression.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/mcp/server.py src/perspicacite/search/scilex_adapter.py tests/unit/test_mcp_search_literature_knobs.py
git commit -m "feat(mcp): search_literature accepts databases kwarg for provider filtering"
```

---

## Task 3: `generate_report` — screen_method / screen_threshold / max_papers_to_download / databases

**Files:**
- Modify: `src/perspicacite/mcp/server.py` (`generate_report` signature ~line 1231)
- Modify: `src/perspicacite/rag/engine.py` — `RAGRequest` dataclass/pydantic model
- Modify: `src/perspicacite/rag/modes/advanced.py` and other mode handlers that consume screening
- Test: `tests/unit/test_mcp_generate_report_knobs.py` (new)

- [ ] **Step 1: Read `RAGRequest` and `generate_report`**

```bash
sed -n '1,40p' /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/engine.py
grep -n "RAGRequest\|class RAGRequest" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/engine.py | head -5
sed -n '1231,1330p' /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/mcp/server.py
```

Note the dataclass/pydantic model for `RAGRequest` and how `generate_report` builds it.

- [ ] **Step 2: Write failing test** at `tests/unit/test_mcp_generate_report_knobs.py`:

```python
"""Tests for new knobs on generate_report MCP tool."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


def _state():
    s = MagicMock()
    s.rag_engine = MagicMock()
    s.rag_engine.execute = AsyncMock(
        return_value=MagicMock(
            answer="ok",
            sources=[],
            mode="advanced",
            usage={"tokens_in": 0, "tokens_out": 0},
        )
    )
    s.session_store = MagicMock()
    return s


async def test_knobs_pass_through_to_rag_request():
    state = _state()
    captured = {}

    async def _capture_execute(request, **kw):
        captured["request"] = request
        return state.rag_engine.execute.return_value

    state.rag_engine.execute = _capture_execute

    with patch.object(mcp_server, "_require_state", return_value=state):
        await mcp_server.generate_report(
            query="x",
            kb_name="kb",
            mode="advanced",
            screen_method="rerank",
            screen_threshold=0.4,
            max_papers_to_download=12,
            databases=["arxiv", "crossref"],
        )

    req = captured["request"]
    assert req.screen_method == "rerank"
    assert req.screen_threshold == 0.4
    assert req.max_papers_to_download == 12
    assert req.databases == ["arxiv", "crossref"]


async def test_invalid_threshold_is_clamped():
    state = _state()
    captured = {}

    async def _capture_execute(request, **kw):
        captured["request"] = request
        return state.rag_engine.execute.return_value

    state.rag_engine.execute = _capture_execute

    with patch.object(mcp_server, "_require_state", return_value=state):
        await mcp_server.generate_report(
            query="x", kb_name="kb",
            screen_threshold=1.5,
            max_papers_to_download=999,
        )

    req = captured["request"]
    assert req.screen_threshold == 1.0
    assert req.max_papers_to_download == 50  # clamped to MAX


async def test_default_knobs_are_none():
    state = _state()
    captured = {}

    async def _capture_execute(request, **kw):
        captured["request"] = request
        return state.rag_engine.execute.return_value

    state.rag_engine.execute = _capture_execute

    with patch.object(mcp_server, "_require_state", return_value=state):
        await mcp_server.generate_report(query="x", kb_name="kb")

    req = captured["request"]
    assert req.screen_method is None
    assert req.screen_threshold is None
    assert req.max_papers_to_download is None
    assert req.databases is None
```

- [ ] **Step 3: Confirm failure**

```bash
uv run pytest tests/unit/test_mcp_generate_report_knobs.py -v
```

- [ ] **Step 4: Extend `RAGRequest`**

Find the `RAGRequest` definition in `src/perspicacite/rag/engine.py` (or wherever it lives — check `rag/__init__.py` for imports too). Add four optional fields with `None` defaults:

```python
screen_method: str | None = None        # "bm25" | "rerank" | "llm"
screen_threshold: float | None = None   # 0.0–1.0
max_papers_to_download: int | None = None  # 1–50
databases: list[str] | None = None      # filtered provider list
```

If `RAGRequest` is a pydantic `BaseModel`, add field validators that clamp `screen_threshold` to [0, 1] and `max_papers_to_download` to [1, 50]. If it's a `@dataclass`, do the clamping in `generate_report`'s wrapper (next step).

- [ ] **Step 5: Modify `generate_report` signature**

Add the four kwargs (all `Optional`, default `None`) to the `@mcp.tool() async def generate_report(...)` signature. Inside the body, before constructing the `RAGRequest`:

```python
        # Clamp invalid values rather than rejecting.
        if screen_threshold is not None:
            screen_threshold = max(0.0, min(1.0, float(screen_threshold)))
        if max_papers_to_download is not None:
            max_papers_to_download = max(1, min(50, int(max_papers_to_download)))
        if screen_method is not None and screen_method not in (
            "bm25", "rerank", "llm"
        ):
            logger.warning(
                "mcp_generate_report_unknown_screen_method",
                method=screen_method,
            )
            screen_method = None
        # `databases` filtering: reuse the KNOWN_DATABASES set from Task 2.
        filtered_databases = None
        if databases is not None:
            from perspicacite.search.scilex_adapter import KNOWN_DATABASES
            filtered_databases = [d for d in databases if d in KNOWN_DATABASES]
            dropped = sorted(set(databases) - KNOWN_DATABASES)
            if dropped:
                logger.warning(
                    "mcp_generate_report_unknown_db", dropped=dropped
                )
            if not filtered_databases:
                filtered_databases = None
```

Pass these through to the `RAGRequest(...)` constructor call.

- [ ] **Step 6: Honor the knobs in mode handlers (minimum-viable)**

For each handler in `src/perspicacite/rag/modes/` that does screening:

```bash
grep -rn "screen\|relevance_method\|screen_threshold" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/modes/ | head -20
```

In each one, where it currently reads config defaults, prefer `request.screen_method or config.<default>` (likewise for threshold and max_papers_to_download). One file at a time; do not refactor unrelated code.

For modes that do NOT screen (e.g. `basic.py`), no change — the request's `screen_method` is None and ignored.

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/unit/test_mcp_generate_report_knobs.py -v
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -15
```

Expected: 3 new tests pass; no regression.

- [ ] **Step 8: Commit**

```bash
git add src/perspicacite/mcp/server.py src/perspicacite/rag/engine.py src/perspicacite/rag/modes/ tests/unit/test_mcp_generate_report_knobs.py
git commit -m "feat(mcp): generate_report accepts screen_method/threshold/max_dl/databases knobs"
```

---

## Task 4: Final-response metadata (`attempts`, `query_rephrasings`, `usage`)

**Files:**
- Modify: `src/perspicacite/mcp/server.py` — both `generate_report` and `search_literature` response builders
- Modify: `src/perspicacite/rag/telemetry.py` — accumulate `attempts`, `query_rephrasings`, and `usage` per request
- Test: `tests/unit/test_mcp_response_metadata.py` (new)

The progress events come and go; consumers that don't process the stream still need the data. We expose three optional response fields populated from the same telemetry stream.

- [ ] **Step 1: Read telemetry**

```bash
sed -n '1,80p' /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/telemetry.py
```

Find the telemetry sink / collector. It likely already collects events — we tap it.

- [ ] **Step 2: Add a per-request collector**

In `src/perspicacite/rag/telemetry.py`, add a small collector class:

```python
class ResponseMetadataCollector:
    """Accumulates response-level metadata from telemetry events.

    Fed alongside the MCPProgressAdapter; produces a dict the MCP tools
    embed in their final JSON response.
    """

    def __init__(self) -> None:
        self._attempts: list[dict] = []
        self._query_rephrasings: list[dict] = []
        self._usage_tokens_in = 0
        self._usage_tokens_out = 0
        self._usage_model: str | None = None
        self._usage_cost_usd: float = 0.0

    async def on_event(self, event: dict) -> None:
        kind = event.get("kind")
        if kind == "provider_progress" and event.get("phase") == "done":
            self._attempts.append(
                {
                    "query": event.get("query"),
                    "provider_counts": dict(event.get("by_provider") or {}),
                    "hit_count": int(event.get("total", 0)),
                }
            )
        elif kind == "query_rephrased":
            self._query_rephrasings.append(
                {
                    "original": event.get("original"),
                    "refined": event.get("rewritten"),
                    "reason": event.get("reason"),
                }
            )
        elif kind == "tokens":
            self._usage_tokens_in += int(event.get("in", 0))
            self._usage_tokens_out += int(event.get("out", 0))
        elif kind == "cost_estimate":
            self._usage_cost_usd += float(event.get("usd", 0.0))
            self._usage_model = event.get("model") or self._usage_model

    def as_response_extras(self) -> dict:
        out: dict = {}
        if self._attempts:
            out["attempts"] = self._attempts
        if self._query_rephrasings:
            out["query_rephrasings"] = self._query_rephrasings
        if self._usage_tokens_in or self._usage_tokens_out or self._usage_cost_usd:
            out["usage"] = {
                "tokens_in": self._usage_tokens_in,
                "tokens_out": self._usage_tokens_out,
                "model": self._usage_model,
                "cost_usd_estimate": round(self._usage_cost_usd, 6),
            }
        return out
```

- [ ] **Step 3: Wire the collector into `generate_report` and `search_literature`**

Each tool already instantiates an `MCPProgressAdapter` somewhere in the body. Right after that, also instantiate the collector and fan-out events to both. If the telemetry layer uses a `TelemetrySink` Protocol (per commit `4aa4433`), pass a list of sinks rather than a single one. Confirm the API by grepping:

```bash
grep -n "TelemetrySink\|class TelemetrySink\|class NullSink" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/telemetry.py
```

Most likely the helper that wires sinks accepts a list; pass `[MCPProgressAdapter(ctx), ResponseMetadataCollector()]`. Keep a reference to the collector and call `.as_response_extras()` after `await execute(...)` completes. Merge the dict into the response payload before `_json_ok(...)`.

- [ ] **Step 4: Write tests** at `tests/unit/test_mcp_response_metadata.py`:

```python
"""Final-response metadata (attempts, query_rephrasings, usage) coverage."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.mcp import server as mcp_server


async def test_generate_report_includes_usage_when_emitted():
    """After running the RAG flow, response carries usage block."""

    state = MagicMock()

    async def _exec_streaming(request, sinks, **kw):
        # Drive the sinks to mimic a real run
        for sink in sinks:
            await sink.on_event(
                {"kind": "tokens", "in": 100, "out": 50}
            )
            await sink.on_event(
                {
                    "kind": "cost_estimate",
                    "usd": 0.01,
                    "model": "deepseek/deepseek-chat",
                }
            )
        return MagicMock(answer="ok", sources=[], mode="advanced")

    state.rag_engine = MagicMock()
    state.rag_engine.execute_with_sinks = _exec_streaming
    state.session_store = MagicMock()

    with patch.object(mcp_server, "_require_state", return_value=state):
        raw = await mcp_server.generate_report(query="x", kb_name="kb")

    payload = json.loads(raw)
    assert payload["success"] is True
    assert payload.get("usage", {}).get("tokens_in") == 100
    assert payload["usage"]["cost_usd_estimate"] == pytest.approx(0.01, rel=1e-3)
```

This test sketch assumes the RAG engine exposes a sinks-aware entry point. If it doesn't, adapt by patching whichever helper wires sinks today (per Step 3 grep). Keep the test focused on the assertion that the collector's output reaches the response.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/test_mcp_response_metadata.py -v
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -15
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/telemetry.py src/perspicacite/mcp/server.py tests/unit/test_mcp_response_metadata.py
git commit -m "feat(mcp): expose attempts/query_rephrasings/usage in final response"
```

---

## Task 5: Emit `phase_progress`, `tokens`, `cost_estimate` events from RAG modes

**Files:**
- Modify: each mode handler in `src/perspicacite/rag/modes/` that the spec table covers
- Modify: `src/perspicacite/rag/engine.py` or `llm/client.py` for token/cost emission

**Scope guard:** if the LLM client already records token counts (commit `4825722` for citation counts; likely a sibling pattern for tokens), tap that. Otherwise emit tokens after each `await llm_client.complete(...)` returns by reading `response.usage` (LiteLLM exposes it).

- [ ] **Step 1: Locate the LLM call sites + token tracking**

```bash
grep -rn "usage\|tokens_in\|tokens_out\|response.usage" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/llm/ | head -10
grep -rn "telemetry.emit\|TelemetrySink\|sink\.on_event" /Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/rag/ | head -15
```

Find where LLM responses are dispatched and where telemetry events are emitted today.

- [ ] **Step 2: Emit tokens after each LLM call**

In the LLM client wrapper (likely `src/perspicacite/llm/client.py`), after a successful `complete(...)` call, if a sink/telemetry hook is in scope, emit:

```python
await telemetry.on_event({
    "kind": "tokens",
    "in": response.usage.prompt_tokens,
    "out": response.usage.completion_tokens,
})
```

If LiteLLM also returns `cost`, emit:

```python
await telemetry.on_event({
    "kind": "cost_estimate",
    "usd": response.cost,  # whatever the LiteLLM field is — verify
    "model": response.model,
})
```

If LiteLLM doesn't return cost, compute from `usage` × `model_pricing` (LiteLLM ships `litellm.completion_cost(response)`). Wrap the cost computation in try/except — log and skip on failure rather than break the request.

- [ ] **Step 3: Emit `phase_progress` events from mode handlers**

For each mode handler:
- `basic.py`: emit `phase_progress` for `retrieve`, `synthesize` (state=running at start, done at end)
- `advanced.py`: emit for `rewrite`, `retrieve_kb`, `retrieve_web`, `screen`, `synthesize`
- `profound.py`: emit for `rewrite`, `retrieve`, `reason`, `synthesize`, `critique`, `revise`
- `agentic.py`: emit for `plan`, `tools`, `synthesize`
- `literature_survey.py`: emit for `collect`, `extract_themes`, `await_selection`, `deepen`
- `contradiction.py`: emit for `search`, `group_by_stance`, `contrast`, `synthesize`

Pattern in each mode (placement is mechanical):

```python
await self._sink.on_event(
    {"kind": "phase_progress", "phase": "retrieve", "state": "running"}
)
# ... existing retrieve logic ...
await self._sink.on_event(
    {"kind": "phase_progress", "phase": "retrieve", "state": "done"}
)
```

Adapt to the actual sink-emission idiom used elsewhere in the file (it likely calls a `self.telemetry.emit(...)` method or yields a `StreamEvent`).

- [ ] **Step 4: Tests are covered by Task 4's collector test**

The collector test exercises tokens + cost_estimate. Phase_progress emission is implementation-level — verifying it would require integration tests that observe the sink. Add one targeted test per mode (focused on the new phase emissions) if the existing mode tests don't already capture it. Otherwise rely on integration tests in `tests/integration/`.

- [ ] **Step 5: Run full unit suite**

```bash
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -20
```

- [ ] **Step 6: Commit per mode (smaller, reviewable diffs)**

```bash
# one commit per mode handler when changes are isolated:
git add src/perspicacite/rag/modes/advanced.py
git commit -m "feat(rag/advanced): emit phase_progress events"

# similarly for other modes...

# final commit for LLM-level tokens + cost
git add src/perspicacite/llm/client.py
git commit -m "feat(llm): emit tokens + cost_estimate telemetry events per completion"
```

---

## Task 6: Final verification

- [ ] **Step 1: All Perspicacité unit tests pass**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
uv run pytest tests/unit/ -v -x -m "not live" 2>&1 | tail -10
```

- [ ] **Step 2: ASB + Scriptorium + audit suites unchanged**

```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder && PYTHONPATH=src python3 -m unittest discover -s tests 2>&1 | tail -10
cd /Users/holobiomicslab/git/Scriptorium && uv run pytest -v 2>&1 | tail -10
```

Expected: no regression. These repos didn't change.

- [ ] **Step 3: Lint**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
uv run ruff check src/perspicacite/mcp/ src/perspicacite/rag/
uv run mypy src/perspicacite/mcp/progress_adapter.py 2>&1 | tail -5
```

- [ ] **Step 4: AGENT_LOG.md entry**

Add a one-line entry to `AGENT_LOG.md` summarizing the change. Do NOT commit anything in `docs/superpowers/`.

---

## Self-review notes

- Task 1 (progress adapter) is small and self-contained — safest place to start.
- Task 2 (`search_literature` databases) and Task 3 (`generate_report` knobs) depend on knowing the `KNOWN_DATABASES` set. Both tasks reference it, so once the adapter exports it, both compile.
- Task 4 (response metadata) and Task 5 (phase_progress emissions) tightly interact — the collector consumes the events the modes emit. Land Task 4's collector first; Task 5 then emits and the test from Task 4 starts producing real values.
- Token/cost emission in Task 5 has the most "depends on existing infrastructure" risk. If `AsyncLLMClient` doesn't already track usage, expand that scope or de-scope cost_estimate to defer until LiteLLM upgrade.
- No consumer-side changes — ASB, Scriptorium, audit continue with their existing calls. Tests in those repos run unchanged.
