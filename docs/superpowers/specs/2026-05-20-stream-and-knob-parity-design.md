# Stream parity & knob parity — design

**Status:** Auto-approved (per user instruction 2026-05-20)
**Date:** 2026-05-20
**Author:** Louis-Félix Nothias (via Claude)
**Scope:** Themes A + B from the 2026-05-20 frontend-vs-MCP brainstorm. Follow-up to the C+D sprint (passage retrieval & structured extraction).
**Affected repo:** `Perspicacite-AI` (server, primary). Consumers (ASB, Scriptorium, audit) are not changed in this sprint — they get the new capabilities passively through their existing MCP calls.

## Motivation

The new Perspicacité front-end (poc/v2b series + `perspicacite_design_1.jsx`) consumes rich SSE frames during streaming RAG calls — phase progress, provider-by-provider hit counts, query rephrases, token + cost meters. It also exposes screen-method / screen-threshold / max-papers-to-download / databases knobs that the equivalent MCP tools don't accept.

C+D fixed the "what tools are exposed" gap. This sprint fixes the **shape and tunability** of existing tools so MCP consumers (audit harness, ASB, Scriptorium) get the same control surface and observability the GUI does.

## Goals

1. **Stream parity (A)** — `generate_report` and `search_literature` emit structured MCP progress events that mirror the front-end SSE frame shapes: `phase_progress`, `provider_progress`, `query_rephrased`, `batch_progress`, plus per-call `tokens` and `cost_estimate`.
2. **Knob parity (B)** — `generate_report` and `search_literature` accept new optional parameters that match the front-end's settings panel: `screen_method`, `screen_threshold`, `max_papers_to_download`, `databases` (the list-of-providers picker).
3. Keep back-compat — all new parameters are optional; existing callers (ASB, Scriptorium today) see no change.

## Non-goals

- Adding new tools (C+D did that).
- Changing the front-end (it already consumes these shapes).
- Surfacing the new knobs in CLI / TUI flows.
- Token cost is reported as an **estimate** based on token counts × LiteLLM-reported per-token pricing — no separate billing integration.

## Architecture

### Component map

```
Perspicacite-AI (server)
├── src/perspicacite/mcp/server.py
│   ├── generate_report             [ENHANCED — new knobs + progress events]
│   └── search_literature           [ENHANCED — new knobs + progress events]
├── src/perspicacite/mcp/progress.py
│   └── MCPProgressAdapter          [EXTENDED — typed event shapes]
├── src/perspicacite/rag/engine.py
│   └── RAGEngine.execute_stream    [MINOR — pass new knobs into mode handler]
├── src/perspicacite/rag/modes/*.py
│   └── Each mode handler           [MINOR — emit new event types where applicable]
└── tests/unit/
    ├── test_mcp_progress_adapter.py [EXTENDED — assert new event types]
    ├── test_mcp_generate_report_knobs.py [NEW]
    └── test_mcp_search_literature_knobs.py [NEW]
```

### A — Stream parity (scoped to what MCP protocol allows)

**Reality check:** MCP's progress notification is `ctx.report_progress(progress: int, total: int, message: str)` — no structured `data` payload. Today `MCPProgressAdapter` (`src/perspicacite/mcp/progress_adapter.py`) handles 4 event kinds (`query_rephrased`, `provider_progress`, `batch_progress`, `rate_limit_low`) and emits human-readable strings.

We make two coordinated changes:

1. **Extend the adapter** to handle 3 additional event kinds the front-end consumes: `phase_progress`, `tokens`, `cost_estimate`. Each becomes a human-readable progress message AND, for structured-data consumers, a JSON tail appended to the `message` field after a `\nMETA:` delimiter. Callers that don't care ignore the tail; callers that do (audit harness, agentic clients) parse it. This is the most faithful representation MCP's wire format allows.

2. **Add final-response metadata.** `generate_report` and `search_literature` JSON responses gain optional fields when present:
   - `attempts`: list of `{query, provider_counts, hit_count}` — one entry per retrieval pass
   - `query_rephrasings`: list of `{original, refined, reason}`
   - `usage`: `{tokens_in, tokens_out, model, cost_usd_estimate}`

This guarantees the data is recoverable even when progress notifications are dropped (slow client, transport hiccup) and matches what audit / batch consumers actually need.

| Event source | Stream channel | Final-response channel |
|---|---|---|
| `phase_progress` | message + META JSON | (absent — phases reconstruct from attempts) |
| `provider_progress` | message + META JSON | `attempts[i].provider_counts` |
| `query_rephrased` | message + META JSON | `query_rephrasings` |
| `batch_progress` | message (existing) | (absent) |
| `tokens` | message + META JSON (final tick) | `usage` |
| `cost_estimate` | message + META JSON (final tick) | `usage.cost_usd_estimate` |

Throttling stays at ≥ 1 s spacing between notifications. The final-tick `tokens`/`cost_estimate` is exempt — we always emit it once at end of stream.

### B — Knob parity

New optional kwargs on `generate_report`:
- `screen_method: Literal["bm25","rerank","llm"] | None = None`
- `screen_threshold: float | None = None` (0.0–1.0)
- `max_papers_to_download: int | None = None`
- `databases: list[str] | None = None` (e.g., `["arxiv","crossref","pubmed"]`)

New optional kwargs on `search_literature`:
- Same `databases: list[str] | None = None`
- Existing `relevance_method` / `min_relevance` stay.

The kwargs flow as follows:
- `screen_method` / `screen_threshold` / `max_papers_to_download` → through `RAGRequest` into mode handlers; each mode handler reads them where applicable. Modes that don't use the knob ignore it gracefully (logged at debug, not warning).
- `databases` → restricts the provider list in the SciLEx adapter / domain aggregator for both tools.

Validation happens in the MCP tool wrapper (clamp `screen_threshold` to [0,1]; clamp `max_papers_to_download` to [1, 50]; warn on unknown `databases` entries but keep the known ones — never crash).

## Tool contracts (changes only)

### `generate_report` — new parameters

```python
@mcp.tool()
async def generate_report(
    query: str,
    kb_name: str = "default",
    mode: str = "advanced",
    recency_weight: float = 0.0,
    kb_names: list[str] | None = None,
    # NEW (all optional, back-compat):
    screen_method: str | None = None,             # bm25 | rerank | llm
    screen_threshold: float | None = None,        # 0.0–1.0
    max_papers_to_download: int | None = None,    # 1–50
    databases: list[str] | None = None,           # ["arxiv","crossref","pubmed",...]
) -> str:
    ...
```

Response shape unchanged on success. Progress events extended (see Stream parity).

### `search_literature` — new parameter

```python
@mcp.tool()
async def search_literature(
    query: str,
    # ... existing params unchanged ...
    databases: list[str] | None = None,            # NEW
) -> str:
    ...
```

Progress events extended.

## Error handling

- Unknown `screen_method` → fall back to server config default; log warning event in progress stream.
- `screen_threshold` outside [0,1] → clamp + log warning event.
- `max_papers_to_download` outside [1,50] → clamp + log warning event.
- `databases` entries not in known list → drop unknown entries + log warning event; if list becomes empty, fall back to server defaults.
- Progress event emission errors are swallowed and logged — never crash the parent RAG call.

## Testing

**`test_mcp_progress_adapter.py` extensions:**
- Adapter accepts structured event dicts (`{"kind": "phase_progress", "phase": "retrieve", "state": "running"}`).
- Adapter wraps plain strings as `{"kind": "log", "text": "..."}`.
- Adapter forwards typed events to `ctx.report_progress` with the right payload shape.

**`test_mcp_generate_report_knobs.py` (new):**
- Each new kwarg is passed through to the mode handler (test by inspecting the constructed `RAGRequest`).
- Invalid values are clamped + a warning is emitted.
- All new kwargs default to None → no behavior change.

**`test_mcp_search_literature_knobs.py` (new):**
- `databases` restricts the SciLEx adapter's provider list (mock the adapter, assert it was called with the filtered set).
- Unknown databases drop with warning; empty fallback to defaults.

Live integration tests stay opt-in (already gated by `-m "not live"`).

## Migration & back-compat

- All new kwargs are optional with `None` default.
- Existing ASB code paths (`get_relevant_passages`, etc.) don't pass these kwargs — they get the same behavior as today.
- Frontend already emits the matching SSE frames; once the MCP tools route them through, the GUI works without changes.
- Audit scenarios in `research-tools-audit` continue to pass with their existing fixtures.

## Out of scope (deferred follow-ups)

- Exposing a `list_databases` or `list_kbs_with_stats` MCP tool (would help the audit harness pick valid databases at runtime). Not blocking.
- Streaming progress through CLI / TUI.
- Cost-tracking persistence (today's cost_estimate is per-call, not summed).
