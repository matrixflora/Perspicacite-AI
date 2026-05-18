# Backend & MCP Hardening — Design

**Date:** 2026-05-18
**Scope:** Tier 1 + Tier 2 + Tier 3 from the audit triage
**Motivation:** GUI testing this session surfaced a set of issues that aren't just UI/wiring bugs — they are silent failures, missing affordances, and architectural inconsistencies in the core backend that affect REST API consumers, the 10 MCP tools, the CLI subcommands, and the planned Mimosa-AI integration. This doc captures a single, focused hardening pass that brings the non-GUI surfaces up to the quality bar the GUI now has.

## Goals

1. **Eliminate silent data-quality failures** — every call path that returns paper metadata should produce the same enriched, provenance-tagged output regardless of caller.
2. **Make long-running MCP calls observable and cancellable** — external agents must be able to see progress and abort.
3. **Expose the affordances the GUI quietly gained** as first-class MCP/API features (web search tool, per-call budgets, structured warnings).
4. **Unify the three diverging web-search code paths** into one shared helper so a fix lands once for everyone.
5. **Loosen architectural coupling** so CLI and MCP work without the web AppState singleton.

## Non-goals

- New RAG modes
- Provider additions
- Front-end work beyond what's needed to consume new MCP/API fields

## Design overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Tier 1: silent bug fixes                       │
│  A1 Crossref enrichment in every search path                          │
│  A2 Structured warnings from SciLEx for dropped APIs                  │
│  A3 PaperContent.attempts surfaced in get_paper_content MCP tool      │
│  A4 _CANCELLED_CHAT_IDS GC (TTL + max size)                           │
│  A5 Profound JSON salvage (port from literature_survey)               │
│  A6 PubMed quota telemetry                                            │
└──────────────────────────────────────────────────────────────────────┘
                                  │
┌──────────────────────────────────────────────────────────────────────┐
│        Tier 2: MCP observability + cancellation + new tool            │
│  B1 MCP progress notifications via ctx.report_progress                │
│  B2 web_search exposed as 11th MCP tool                               │
│  B3 MCP cancellation via shared cancellation registry                 │
│  B4 Per-call budget / parallelism params on generate_report           │
└──────────────────────────────────────────────────────────────────────┘
                                  │
┌──────────────────────────────────────────────────────────────────────┐
│            Tier 3: architectural cleanup                              │
│  B5 Typed metadata.sources on Paper model                             │
│  B6 AppState injection (remove global imports from mode code)         │
│  B7 Google Scholar citation-count extraction                          │
│  C3 Unified web-search code path (single _resolve_papers helper)      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Tier 1: silent bug fixes

### A1. Crossref enrichment in every search path

**Problem.** `_canonicalize_candidates_from_crossref` lives in `rag/modes/basic.py` and only runs inside `_web_fallback_papers`. Other search entry points return raw provider data:

- `rag/agentic/orchestrator.py::_scilex_search` → returns SciLEx papers unenriched
- `rag/modes/literature_survey.py::_broad_search` → returns SciLEx + standalone papers unenriched
- `mcp/server.py::search_literature` tool → returns whatever the aggregator gave back

For Google Scholar this is catastrophic: no DOI → no abstract → unusable for downstream synthesis.

**Design.**

1. Extract `_canonicalize_candidates_from_crossref` + `_backfill_dois_from_crossref` into a new module `pipeline/enrichment/crossref_enrich.py`. Keep the existing functions as one-line re-exports from `basic.py` for back-compat.

2. New public helper: `async def enrich_papers(papers: list[Paper]) -> list[Paper]`.
   - Operates on `Paper` objects (not dict candidates) — converts internally if needed.
   - Same shared semaphore + spacing throttle + `CROSSREF_MAILTO`/`UNPAYWALL_EMAIL` polite-pool detection.
   - Sets `paper.metadata["enrichment_sources"] = ["crossref", ...]`.
   - Returns the same list with in-place enrichment (no copy).

3. Wire `enrich_papers` into:
   - `agentic/orchestrator.py::_scilex_search` — after the SciLEx call, before relevance scoring.
   - `literature_survey.py::_broad_search` — after the merged fan-out, before `_convert_to_candidates`.
   - `mcp/server.py::search_literature` — after the underlying aggregator call.

4. Add an `enrich: bool = True` request flag to `mcp/server.py::search_literature` so MCP consumers can opt out if they want raw provider data for some reason (rare; default on).

**Acceptance.** A `search_literature(query="feature-based molecular networking", apis=["google_scholar"])` MCP call returns papers with non-null `abstract` for every paper Crossref recognizes.

### A2. Structured warnings from SciLEx for dropped APIs

**Problem.** `SciLExAdapter.search` logs `scilex_filtered_non_scilex_apis` when the caller passes APIs SciLEx doesn't know about, then silently proceeds. MCP callers have no way to know their `apis=["google_scholar","semantic_scholar"]` was secretly turned into `apis=["semantic_scholar"]`.

**Design.**

1. New return type for the *adapter layer only* (NOT for higher-level callers — we don't want to break basic/advanced/profound):
   ```python
   @dataclass
   class SciLExSearchResult:
       papers: list[Paper]
       dropped_apis: list[str]  # APIs caller asked for that SciLEx doesn't know
   ```

2. Add a sibling method `SciLExAdapter.search_with_warnings()` returning the new type. Keep `search()` returning `list[Paper]` unchanged for back-compat.

3. In `mcp/server.py::search_literature`, call `search_with_warnings`, attach `dropped_apis` to the response payload under a new `warnings` field:
   ```json
   {
     "papers": [...],
     "warnings": [
       {"kind": "unknown_apis_dropped", "apis": ["google_scholar"],
        "advice": "Use the new web_search MCP tool for Google Scholar."}
     ]
   }
   ```

4. In `agentic/orchestrator.py`, log the warning at INFO level with a hint about the user's selection so the agentic log makes the silent drop visible.

**Acceptance.** MCP call `search_literature(apis=["semantic_scholar","google_scholar"])` returns `warnings: [{kind: "unknown_apis_dropped", apis: ["google_scholar"]}]`.

### A3. Surface `PaperContent.attempts` in `get_paper_content`

**Problem.** `PaperContent` records every download attempt with `{source, status, error?}` via `record_attempt`. The MCP `get_paper_content` tool serializes `PaperContent` but doesn't expose `_attempts` (it's a private `__post_init__` field). Consumers see "content_type: none" and can't diagnose.

**Design.**

1. Promote `attempts` to a public field on `PaperContent`:
   ```python
   @dataclass
   class PaperContent:
       ...
       attempts: list[dict[str, Any]] = field(default_factory=list)
   ```
   Drop the `__post_init__`/`_attempts`/`property` machinery.

2. Add `attempts` to the MCP tool response payload. Each entry already has `{source, status, error?}` and arbitrary `**extra`.

3. The HTML side panel's "no content" message already exists; add a small attempts table when `attempts` is non-empty.

**Acceptance.** `get_paper_content(doi="10.1234/...")` MCP response includes an `attempts` array. Failing-by-paywall papers show 3-5 attempts with the relevant `error` per source.

### A4. `_CANCELLED_CHAT_IDS` garbage collection

**Problem.** Memory leak: `web/routers/chat.py::_CANCELLED_CHAT_IDS: set[str]` grows forever.

**Design.**

1. Replace `set[str]` with `dict[str, float]` mapping chat-id → cancel timestamp.
2. On insertion, prune entries older than 1 hour AND cap at 1000 entries (LRU-style: drop oldest by timestamp).
3. `is_chat_cancelled` reads from the dict.
4. Add a unit test that inserts 10k IDs and asserts size ≤ 1000.

**Acceptance.** Continuous load test sending 100 cancellations/sec for 10 min keeps memory flat.

### A5. Profound JSON salvage

**Problem.** Log shows `Invalid control character at: line 8 column 11 (char 1187)` in `profound._analyze_documents_json`. The whole step fails and the consecutive-failure counter trips, cutting the cycle short. Literature_survey has `_salvage_truncated_json`; profound doesn't.

**Design.**

1. Extract `_salvage_truncated_json` from `literature_survey.py` into a new `rag/utils/json_salvage.py` with two helpers:
   - `salvage_truncated_array(json_str: str, array_key: str) -> list[dict] | None` — recovers complete `{...}` objects from a truncated array.
   - `clean_control_chars(json_str: str) -> str` — strips raw `\x00-\x1f` control characters that some LLMs emit inside string values.

2. Wire into `profound._analyze_documents_json` and `profound._create_plan` (which also parses JSON).

3. Also wire `clean_control_chars` into `literature_survey._analyze_batch` (current `_fix_json` doesn't handle it).

**Acceptance.** Re-run the failing query in profound; the step that previously errored now succeeds with a `profound_analyze_json_salvaged` info log.

### A6. PubMed quota telemetry

**Problem.** SciLEx hits NCBI's per-key quota. Log: `"PubMed API: Only 2 requests remaining in current period!"`. Buried in SciLEx's internal logger; not surfaced to caller.

**Design.**

1. In `scilex_adapter.py`, wrap the SciLEx call with a log capture context that scans for the quota warning regex (`r"Only (\d+) requests remaining"`).
2. When matched, attach a structured warning to the result:
   ```python
   {"kind": "rate_limit_low", "provider": "pubmed", "remaining": 2,
    "advice": "Add NCBI_API_KEY to lift quota from 3 r/s to 10 r/s."}
   ```
3. Drain this warning into the new `warnings: [...]` field on MCP responses (Tier 1.A2).
4. Also emit via SSE telemetry to GUI (`provider_rate_limit_warning` kind).

**Acceptance.** Burst 20 PubMed queries in a row, observe `warnings: [{kind: "rate_limit_low", provider: "pubmed", ...}]` in the response.

---

## Tier 2: MCP observability + cancellation + new tool

### B1. MCP progress notifications

**Problem.** Long-running MCP tools (`generate_report`, `search_to_kb`) are black boxes. The GUI now sees `query_rephrased`, `provider_progress`, `batch_progress`, `source`, `status` events; MCP gets only the final result.

**Design.**

1. `fastmcp` 3.x (already pinned in `pyproject.toml`) supports `Context` injection in tools with `ctx.report_progress(progress: int, total: int, message: str)` — verified present on `mcp.server.fastmcp.Context`.

2. Add `ctx: Context` parameter to `generate_report`, `search_literature`, and `search_to_kb`.

3. Define a small adapter `progress_adapter.py`:
   ```python
   class MCPProgressAdapter:
       def __init__(self, ctx: Context):
           self.ctx = ctx
           self._tot = 0
           self._n = 0

       async def on_event(self, ev: dict):
           # Maps internal kinds → MCP progress
           kind = ev.get("kind")
           if kind == "batch_progress":
               cur, tot = ev.get("current", 0), ev.get("total", 1)
               await self.ctx.report_progress(cur, tot, f"{ev.get('stage', 'batch')}: {cur}/{tot}")
           elif kind == "provider_progress" and ev.get("phase") == "done":
               by = ev.get("by_provider", {})
               msg = ", ".join(f"{k}: {v}" for k, v in by.items())
               await self.ctx.report_progress(self._n, self._tot, f"DB results: {msg}")
           # ... etc
   ```

4. Plumb the adapter into the existing `telemetry: list[dict]` pattern by replacing the list with a `TelemetrySink` protocol that supports both `append` (list-style) and `on_event_async` (callback-style). All existing modes keep working; MCP gets live events.

5. Define `rag/telemetry.py::TelemetrySink` protocol; existing list usages adapt via a `ListTelemetrySink` wrapper.

**Acceptance.** An MCP client polling `progressToken` sees periodic `progress` notifications during a 5-min profound run.

### B2. `web_search` as the 11th MCP tool

**Problem.** External agents who want to "look up some papers about X" can only call `search_literature` (SciLEx-only, no Google Scholar) or `generate_report` (heavy, mode-bound). They can't directly use the aggregator + Crossref enrichment + rerank pipeline.

**Design.**

1. New MCP tool `web_search`:
   ```python
   @mcp.tool()
   async def web_search(
       query: str,
       databases: list[str] = None,  # default: [semantic_scholar, openalex, pubmed]
       max_results: int = 10,
       enrich: bool = True,
       optimize_query: bool = True,
       ctx: Context = None,
   ) -> dict:
       """Live academic web search across user-selected databases.
       Includes Google Scholar with Playwright/OpenRouter fallback.
       Returns enriched, deduplicated, reranked results."""
   ```

2. Internally calls `rag.web_search.run_web_aggregator_search` + the new shared `enrich_papers` helper from A1 + the existing rerank from `_web_fallback_papers`.

3. Response payload includes `papers`, `warnings`, `telemetry_summary` (provider counts).

4. Update `docs/perspicacite_skills.md` to document the new tool.

**Acceptance.** MCP `web_search(query="...", databases=["google_scholar", "europepmc"])` returns enriched papers with `sources_all` chips.

### B3. MCP cancellation

**Problem.** No way to cancel an in-flight `generate_report`. Currently only the GUI's `/api/chat/cancel` route works.

**Design.**

1. Promote `_CANCELLED_CHAT_IDS` (now a dict per A4) to a process-wide registry in `rag/cancellation.py::CancellationRegistry`. Public API:
   ```python
   def mark_cancelled(task_id: str) -> None
   def is_cancelled(task_id: str) -> bool
   def register_task(task_id: str) -> CancellationToken
   ```

2. Add a new MCP tool `cancel_task(task_id: str)`. Returns `{"ok": true, "was_running": bool}`.

3. Long-running MCP tools (`generate_report`, `search_to_kb`, `web_search`) accept an optional `task_id: str` param. If not provided, one is generated and returned in the *first progress notification* so clients can use it to cancel.

4. Cancellation points: at the start of each RAG cycle, each batch in literature_survey, each agentic iteration. Use existing `check_cancellation()` calls; switch from `_CANCELLED_CHAT_IDS.get` to the registry.

5. The chat router migrates to use the same registry, removing duplication.

**Acceptance.** Start a 5-min `generate_report(mode="profound")` MCP call, call `cancel_task(task_id)` after 30 s, original call returns within 5 s with `cancelled: true`.

### B4. Per-call budget / parallelism params

**Problem.** Profound's `max_total_seconds`, `max_iterations`, literature_survey's `batch_size`, etc. are config-file values only.

**Design.**

1. Extend `RAGRequest` (Pydantic model in `models/rag.py`) with optional override fields:
   ```python
   max_total_seconds: float | None = None  # overrides config
   max_iterations: int | None = None
   batch_size: int | None = None
   crossref_concurrency: int | None = None
   ```

2. Each mode reads `request.max_total_seconds or self.max_total_seconds` etc.

3. `mcp/server.py::generate_report` adds matching optional params and passes them into `RAGRequest`.

4. Validate ranges in the model: `max_iterations: 1 ≤ N ≤ 10`, `max_total_seconds: 30 ≤ T ≤ 1800`, etc.

**Acceptance.** `generate_report(mode="profound", max_iterations=1, max_total_seconds=120)` runs in under 2 min.

---

## Tier 3: architectural cleanup

### B5. Typed `metadata.sources` on `Paper`

**Problem.** Provenance info is shoved into `paper.metadata["sources"]` (free-form dict key) on Paper, while `SourceReference` has the typed `sources_all: list[str] | None` plus `enrichment_sources: list[str] | None`. Two parallel conventions for the same data.

**Design.**

1. Promote to first-class fields on `Paper`:
   ```python
   class Paper(BaseModel):
       ...
       discovery_sources: list[str] = Field(default_factory=list)  # which DBs returned this
       enrichment_sources: list[str] = Field(default_factory=list)  # which DBs enriched it
   ```

2. Keep `metadata["sources"]` populated for one release as a back-compat shim with a deprecation log.

3. Update all aggregator code paths (domain_aggregator, scilex_adapter pre-dedupe map) to populate the new fields directly.

4. `SourceReference.sources_all` is renamed to `discovery_sources` (with field alias for wire back-compat).

5. Migration: add a deprecation test that fails if `metadata["sources"]` is read from anywhere in `src/` (forces the migration).

**Acceptance.** `mypy --strict` passes on `paper.discovery_sources`; nothing in `src/` reads `metadata["sources"]`.

### B6. AppState injection (remove global imports from mode code)

**Problem.** `from perspicacite.web.state import app_state as _global_app` appears in basic.py, advanced.py, profound.py, web_search.py, rag/tools/__init__.py. Breaks CLI / MCP isolated use, hurts testability.

**Design.**

1. Add `app_state: Any = None` to `RAGRequest`. Already partially supported; formalize it.

2. `RAGEngine.execute_stream(request)` always sets `request.app_state = self.app_state` before dispatching to the mode handler if it isn't set.

3. Modes read `request.app_state` instead of importing globally. The fallback-to-global-import lines are removed.

4. `WebSearchTool.__init__(app_state=...)` becomes required; the global lookup falls away.

5. CLI and MCP entry points construct a `MinimalAppState` (just `config`, `llm_client`, no FastAPI baggage) and pass it through.

**Acceptance.** `grep -r "from perspicacite.web.state" src/perspicacite/rag/` returns zero hits. CLI subcommand `screen-papers` works with the new path.

### B7. Google Scholar citation-count extraction

**Problem.** GS snippets contain "Cited by N" but the parser doesn't extract it. Ranking weights citations at 25%; GS hits silently score 0 here.

**Design.**

1. In `google_scholar_playwright.py::_render_and_extract_cards`, extract the citation count from the `.gs_fl` footer row (typically the second `<a>` link, text matches `r"^Cited by (\d+)$"`).
2. Store in `Paper.citation_count`.
3. Add the same extraction to the OpenRouter Exa fallback path (parse the snippet text).
4. Add a unit test with a saved GS HTML fixture.

**Acceptance.** GS results for "feature-based molecular networking" return `citation_count > 0` for at least 3 of the top 5 hits.

### C3. Unified web-search code path

**Problem.** Three diverging implementations:
- `basic/advanced` → `_web_fallback_papers` (full pipeline: aggregator + Crossref + DOI backfill + rerank + relevance)
- `profound` → raw `WebSearchTool.execute` (no enrichment, no rerank, no relevance filter)
- `literature_survey` → hand-rolled SciLEx + standalone fan-out (now after this session) (no Crossref enrichment, no rerank)

**Design.**

1. New canonical helper `rag/web_search.py::resolve_papers_pipeline`:
   ```python
   async def resolve_papers_pipeline(
       query: str,
       databases: list[str],
       *,
       max_docs: int,
       app_state: Any,
       telemetry: TelemetrySink | None = None,
       enrich: bool = True,
       rerank: bool = True,
       min_relevance: float = 0.0,
   ) -> list[Paper]:
       """One pipeline: aggregator fan-out → Crossref enrich → MiniLM rerank → relevance gate."""
   ```

2. Refactor:
   - `_web_fallback_papers` → 5-line wrapper around `resolve_papers_pipeline`.
   - `profound`'s web search → calls `resolve_papers_pipeline` via the WebSearchTool.
   - `literature_survey._broad_search` → calls `resolve_papers_pipeline` (it already does the work, just inline).
   - The new MCP `web_search` tool (B2) → also calls it.

3. Returns `list[Paper]` not `list[dict]` (Paper is the typed model). Callers that need dict shape do the conversion themselves with a small adapter.

4. Telemetry flows through `TelemetrySink` (per B1) so MCP and SSE both get live events.

**Acceptance.** All four search call sites use `resolve_papers_pipeline`. A regression test asserts identical output for the same query across all four entry points.

---

## Risks & mitigations

| Risk | Mitigation |
|------|------------|
| **Tier 3 refactors break existing tests** | Run full test suite after each change; keep back-compat shims for one release. |
| **MCP progress notifications increase token usage on slow clients** | Throttle progress emissions to ≥1s spacing. |
| **`enrich_papers` adds 2-5s latency to every search** | Make it opt-out via `enrich=False`; runs in parallel with rerank where possible. |
| **Cancellation registry race conditions** | Use `asyncio.Lock` around the dict; cancellation tokens are idempotent. |
| **GS citation extraction is brittle (Google changes HTML)** | Wrap in try/except; missing count silently falls back to 0 (no regression vs today). |

## Testing strategy

- **Unit tests** (≥ 50 new): one per new helper, one per back-compat shim, one per regression scenario from this session (Google Scholar without DOI, SciLEx with unknown API, profound JSON with control char, etc.)
- **MCP live test extension** (`tests/test_mcp_live.py`): new test cases for `web_search` tool, `cancel_task`, progress notifications.
- **Soak test** for A4: 10k cancellations, assert memory bounded.
- **Cross-mode regression** for C3: same query through all four entry points, assert same paper set.

## Migration / rollout

Single PR per Tier, each independently mergeable:
- **PR 1 (Tier 1):** ~5 days. Low risk, immediate quality wins. No API breakage.
- **PR 2 (Tier 2):** ~5 days. New MCP surface area. No breakage but consumers need to adopt to benefit.
- **PR 3 (Tier 3):** ~7 days. Touches mode code; back-compat shims for one release; full test suite must pass.

Total estimate: ~2.5 weeks of focused work.

## Out of scope (deferred)

- C1 (profound result-count proportional to research depth) — UX tweak, doesn't affect contracts
- C2 (relevance-score caching) — perf optimization, defer until profiled
- C4 (SciLEx tmpdir cache invalidation) — needs SciLEx changes, escalate upstream

## Open questions

None for this design — all decisions are made above. Discoveries during implementation will be flagged in the plan doc.
