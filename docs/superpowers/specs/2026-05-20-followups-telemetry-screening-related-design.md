# Follow-Ups: Search Telemetry, Screening Knobs, Related Papers, Profound Phases — Design

> **Status:** Spec. Working document — DO NOT commit to git.
> **Date:** 2026-05-20
> **Author:** autonomous sprint (continuation of passage-retrieval + stream/knob-parity work)

## Motivation

The 2026-05-20 multi-repo sprint left four documented follow-ups where plumbing
was shipped but the consuming end was a no-op. Each is now actionable because the
upstream pieces (telemetry sink protocol, screening functions, `search_literature`
tool, `emit_phase` helper) already exist. This spec closes the four gaps with the
minimal change that makes each piece actually do its job.

## Scope

Four independent fixes, three tasks (two share `profound.py`):

1. **Search telemetry** — populate the `usage` block on `search_literature`
   responses by threading the response collector into the query optimizer's LLM call.
2. **Profound screening knobs** — make profound mode consume `screen_method` and
   `screen_threshold` from `RAGRequest` instead of hardcoding rerank + `0.0`.
3. **Profound cycle phase_progress** — emit `phase_progress` events for the
   per-cycle reflect/critique step that currently runs silently.
4. **ASB related papers** — implement `search_related_papers` on the ASB client by
   calling the existing `search_literature` MCP tool, replacing the always-empty stub.

Out of scope: building a new server-side semantic "related papers" tool (citation
expansion already exists separately; `search_literature` covers the query→papers
need the callers have). Advanced/basic/agentic mode screening (profound is the
only mode with a real filtering step today). Changing the telemetry event schema.

## Design

### 1. Search telemetry (`search_literature` usage)

**Current state.** `mcp/server.py` `search_literature` creates
`_response_collector = ResponseMetadataCollector()` and merges
`_response_collector.as_response_extras()` into the payload at the end, but never
passes it anywhere. The collector populates `usage` only from `tokens` /
`cost_estimate` events. The only LLM call in the `search_literature` path is in
`query_optimizer.optimize_query()` (`llm_client.complete(...)`), which does not
forward a `sink=`. The aggregator is pure HTTP — no LLM, no telemetry source.

**Change.**
- Add an optional keyword-only `sink: Any = None` parameter to
  `optimize_query(...)` in `src/perspicacite/search/query_optimizer.py`.
- When `sink` is not None, pass `sink=sink` into the `app_state.llm_client.complete(...)`
  call. The LLM client already emits `tokens` + `cost_estimate` to a provided sink.
- In `search_literature` (`mcp/server.py`), pass `sink=_response_collector` into the
  `optimize_query(...)` call.

**Result.** When query optimization runs and makes its LLM call, the resulting
`tokens`/`cost_estimate` events flow into the collector, so `usage` is populated on
the final response. When optimization is disabled or short-circuits before the LLM
call, `usage` is omitted (collector stays empty) — unchanged, correct behavior.

**Error handling.** `sink=None` default preserves all existing call sites. If the
LLM call fails, the optimizer's existing fallback path runs; no telemetry is emitted,
`usage` is simply absent. No new failure modes.

### 2. Profound screening knobs

**Current state.** `profound.py` `_filter_documents_by_relevance()` calls
`screen_papers_rerank(...)` with a hardcoded `threshold=0.0`. `RAGRequest` exposes
`screen_method: str | None` (`"bm25" | "rerank" | "llm"`) and
`screen_threshold: float | None` ∈ [0,1], both defaulting to `None`. No mode reads them.

**Change.** In `_filter_documents_by_relevance()`:
- Read `method = request.screen_method` and `threshold = request.screen_threshold`.
- Resolve effective threshold: `threshold if threshold is not None else 0.0`
  (preserves current default).
- Route on method:
  - `None` or `"rerank"` → `screen_papers_rerank(..., threshold=effective_threshold)`
    (current behavior, now threshold-configurable).
  - `"bm25"` → `screen_papers(..., threshold=effective_threshold)`.
  - `"llm"` → `screen_papers_llm(..., threshold=effective_threshold)`.
- The unconditional preservation of KB-sourced docs and the top-N tail behavior are
  unchanged — only the screening function and threshold are parameterized.

**Constraint.** Whatever extra arguments `screen_papers` / `screen_papers_llm`
require (query text, llm client, etc.) must be sourced from the same context
`_filter_documents_by_relevance` already has. If a method's dependencies are not
available in that scope, fall back to rerank rather than failing. The implementer
must read `search/screening.py` signatures and adapt; do not invent arguments.

### 3. Profound cycle phase_progress

**Current state.** The cycle loop (`for cycle in range(self.max_cycles)`) runs
plan → execute steps → iteration summary (`_create_iteration_summary`, the
reflect/critique analog) with no `phase_progress` between the `retrieve`/`reason`
phases. Other modes emit paired `running`/`done` events via
`emit_phase(sink, phase=..., state=..., **extra)`.

**Change.** Inside the cycle loop, wrap the reflection/iteration-summary step with
`emit_phase` calls on a `reflect` phase, carrying the cycle index as extra context:
- Before `_create_iteration_summary(...)`:
  `emit_phase(_phase_sink, phase="reflect", state="running", cycle=cycle)`
- After it returns:
  `emit_phase(_phase_sink, phase="reflect", state="done", cycle=cycle)`

Use the existing `_phase_sink` already extracted in `execute_stream`. Guard exactly
as existing `emit_phase` calls in the file are guarded (same sink-None handling).
Do not rename existing phases or alter their emissions.

**Result.** Consumers (MCPProgressAdapter) see per-cycle reflect progress. The
`cycle` extra rides along in the event dict, matching the `**extra` pattern.

### 4. ASB related papers via search_literature

**Current state.** `MCPPerspicaciteClient.search_related_papers` calls a nonexistent
`search_related_papers` MCP tool, catches `RuntimeError`, and always returns `[]`.
Callers (`workflow_composer.py`, `skill_pack_v3.py`) pass a query/topic string and
expect `list[RelatedPaper]` with `.doi/.title/.year/.score`.

**Change.** Reimplement `MCPPerspicaciteClient.search_related_papers` to call the
existing `search_literature` MCP tool:
- `raw = self._sess().call_tool("search_literature", {"query": query, "max_results": k})`
  (use the real `search_literature` parameter names — implementer must confirm them
  against the server tool signature; `query` + a result-count arg).
- Parse the response the same defensive way the other client methods do (dict with a
  results/papers key, or a bare list).
- Map each row to `RelatedPaper(doi=..., title=..., year=..., score=...)`, defaulting
  `score` to `0.0` when absent and skipping rows with neither doi nor title.
- Keep the `try/except RuntimeError: return []` guard so a missing/unavailable server
  still degrades gracefully.
- Update the docstring to reflect that it now wraps `search_literature` (remove the
  "no analog / out-of-scope" note).

The `PerspicaciteClient` Protocol signature and `MockPerspicaciteClient` are
unchanged (the mock already returns its canned list).

## Testing

- **Telemetry:** Unit test that `optimize_query` forwards `sink=` to the LLM client
  (patch the client, assert `sink` received). Integration-style test that a
  `search_literature` run with optimization enabled and a stubbed LLM emitting
  tokens/cost yields a non-empty `usage` block; with optimization disabled, `usage`
  is absent.
- **Screening knobs:** Tests that `_filter_documents_by_relevance` dispatches to the
  correct screening function per `screen_method` and passes `screen_threshold`
  through; that `None` method preserves current rerank behavior; that an unsupported
  method falls back to rerank.
- **Profound phases:** Test that running a profound cycle emits paired
  `reflect` running/done `phase_progress` events with the `cycle` index, captured via
  a fake telemetry sink.
- **ASB related papers:** Test that `search_related_papers` calls `search_literature`
  with the right args and maps rows to `RelatedPaper`; that a `RuntimeError` yields
  `[]`; that malformed rows are skipped.

All existing suites must stay green: Perspicacite-AI pytest (`asyncio_mode=auto`,
no `@pytest.mark.asyncio`), ASB unittest.

## Risks

- Profound is a hot path; screening-knob and phase changes carry regression risk →
  keep spec + quality review on Task 2.
- `screen_papers` / `screen_papers_llm` may need context not present in
  `_filter_documents_by_relevance`; the fallback-to-rerank rule contains that risk.
- `search_literature` response shape must be confirmed against the actual tool; the
  implementer reads the server signature rather than assuming.
