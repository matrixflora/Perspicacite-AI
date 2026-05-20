# Follow-Ups Implementation Plan: Telemetry, Screening Knobs, Related Papers, Profound Phases

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **DO NOT commit anything in docs/superpowers/** — spec and plan are local working documents.

**Goal:** Close four shipped-but-inert follow-ups: search_literature `usage` telemetry, profound screening knobs, profound cycle phase_progress, and ASB `search_related_papers`.

**Architecture:** Each fix threads an already-existing mechanism (telemetry sink, screening functions, `emit_phase`, `search_literature` tool) into the place that should have consumed it. No new schemas, no new server tools. Minimal, surgical edits with TDD.

**Tech Stack:** Python, FastMCP, pytest (`asyncio_mode=auto`, no `@pytest.mark.asyncio` decorators) for Perspicacite-AI; unittest for ASB.

**Repos:**
- Perspicacite-AI: `/Users/holobiomicslab/git/Perspicacite-AI` — branch `dev_v2b` (continue on it)
- AgenticScienceBuilder: `/Users/holobiomicslab/git/AgenticScienceBuilder` — branch `feat/persp-passage-extraction-2026-05-20` (continue on it)

**Task ordering:** Task 1 and Task 2 both modify Perspicacite-AI but disjoint files (`query_optimizer.py`/`server.py` vs `profound.py`) — run sequentially to avoid interleaved commits in one repo. Task 3 is a separate repo and may run in parallel.

---

### Task 1: search_literature usage telemetry

**Files:**
- Modify: `src/perspicacite/search/query_optimizer.py` (add `sink` kwarg, forward to `complete`)
- Modify: `src/perspicacite/mcp/server.py` (`search_literature`, pass `sink=_response_collector` into `optimize_query`)
- Test: `tests/` — add a test module, e.g. `tests/search/test_query_optimizer_telemetry.py` (match existing test layout; if `tests/search/` doesn't exist, place beside existing query optimizer tests — grep first)

- [ ] **Step 1: Read current code**

Read `src/perspicacite/search/query_optimizer.py` (the `optimize_query` function and its `app_state.llm_client.complete(...)` call) and `src/perspicacite/mcp/server.py` around the `search_literature` tool (the `_response_collector` creation, the `optimize_query(...)` call site, and the final `payload.update(_response_collector.as_response_extras())`). Confirm the exact `optimize_query` signature and the exact `complete(...)` call.

- [ ] **Step 2: Write the failing test (optimizer forwards sink)**

Find existing query-optimizer tests (grep `optimize_query` under `tests/`). Add a test that patches `app_state.llm_client.complete` with an AsyncMock and asserts that when `optimize_query(..., sink=my_sink)` is called, `complete` was awaited with `sink=my_sink` in its kwargs. Build the minimal `app_state` / inputs the existing tests use (reuse their fixtures/builders).

```python
async def test_optimize_query_forwards_sink_to_llm():
    sink = []
    app_state = _build_app_state_with_mock_llm()  # reuse existing helper/fixture
    app_state.llm_client.complete = AsyncMock(return_value="refined query")
    await optimize_query(
        query="original",
        context=None,
        app_state=app_state,
        sink=sink,
    )
    _, kwargs = app_state.llm_client.complete.call_args
    assert kwargs.get("sink") is sink
```

Adapt argument names to the real `optimize_query` signature discovered in Step 1.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/holobiomicslab/git/Perspicacite-AI && python -m pytest tests/search/test_query_optimizer_telemetry.py -v` (adjust path)
Expected: FAIL — `optimize_query` does not accept `sink` (TypeError) or `complete` not called with `sink`.

- [ ] **Step 4: Implement sink threading in optimizer**

In `optimize_query`, add a keyword-only `sink: Any = None` parameter. In the `app_state.llm_client.complete(...)` call, add `sink=sink`. The LLM client already does `sink = kwargs.pop("sink", None)` and emits telemetry when non-None, so passing `sink=None` is harmless.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/search/test_query_optimizer_telemetry.py -v`
Expected: PASS

- [ ] **Step 6: Write the failing test (search_literature populates usage)**

Find existing `search_literature` tests (grep under `tests/`). Add a test that invokes `search_literature` with query optimization enabled, stubbing the LLM client so its `complete` emits a `tokens` event and a `cost_estimate` event to whatever sink it receives (i.e., the stub calls `emit_tokens(sink, ...)` / `emit_cost(sink, ...)` or appends the raw event dicts). Assert the returned payload contains a non-empty `usage` block with `tokens_in`/`tokens_out`. Also assert that when optimization is disabled (or the LLM is not called), `usage` is absent.

Reuse the existing search_literature test scaffolding (mock aggregator returning a couple of `Paper`s, mock app_state). Confirm how those tests enable/disable optimization and mirror it.

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest tests/.../test_search_literature*.py -v` (use the real path)
Expected: FAIL — `usage` absent because collector not wired.

- [ ] **Step 8: Wire collector into optimize_query call in server.py**

In `search_literature`, change the `optimize_query(...)` call to pass `sink=_response_collector`. Leave the final `payload.update(_response_collector.as_response_extras())` as-is.

- [ ] **Step 9: Run both tests + adjacent suites**

Run: `python -m pytest tests/search/ tests/mcp/ -v` (adjust to real dirs; include the query optimizer and search_literature test modules)
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
git add src/perspicacite/search/query_optimizer.py src/perspicacite/mcp/server.py tests/
git commit -m "feat(search): populate search_literature usage telemetry via optimizer sink"
```

---

### Task 2: Profound mode — screening knobs + cycle phase_progress

**Files:**
- Modify: `src/perspicacite/rag/modes/profound.py` (`_filter_documents_by_relevance` ~line 2386; cycle loop ~lines 691–903)
- Read-only reference: `src/perspicacite/search/screening.py` (signatures of `screen_papers`, `screen_papers_rerank`, `screen_papers_llm`)
- Read-only reference: `src/perspicacite/rag/telemetry.py` (`emit_phase` signature)
- Test: existing profound test module (grep `profound` under `tests/`); add cases there or in a sibling module.

- [ ] **Step 1: Read current code**

Read `_filter_documents_by_relevance` in `profound.py` (full body), the cycle loop in `execute_stream` (the `for cycle in range(self.max_cycles)` block and the `_create_iteration_summary` call site), the `_phase_sink` extraction, and the three screening function signatures in `search/screening.py`. Note exactly which arguments each screening function needs (query text, llm client, top_n, etc.) and which are available inside `_filter_documents_by_relevance`.

- [ ] **Step 2: Write failing test (screen_method routing)**

In the profound test module, add tests that call `_filter_documents_by_relevance` (or the smallest entry point that reaches it) with a `RAGRequest` carrying:
  (a) `screen_method=None` → asserts `screen_papers_rerank` is invoked (patch it) with `threshold=0.0`.
  (b) `screen_method="bm25", screen_threshold=0.4` → asserts `screen_papers` invoked with `threshold=0.4`.
  (c) `screen_method="llm", screen_threshold=0.6` → asserts `screen_papers_llm` invoked with `threshold=0.6` (skip/fallback-assert if llm client unavailable in that scope — see Step 4).
  (d) `screen_method="bogus"` → asserts fallback to `screen_papers_rerank`.

Patch the screening functions where profound imports them. Build the minimal documents list + request the existing profound tests use.

```python
def test_filter_documents_uses_bm25_when_requested(monkeypatch):
    called = {}
    def fake_screen_papers(docs, query, *, threshold, **kw):
        called["threshold"] = threshold
        return docs
    monkeypatch.setattr(profound_module, "screen_papers", fake_screen_papers)
    request = _build_request(screen_method="bm25", screen_threshold=0.4)
    mode = _build_profound_mode()
    mode._filter_documents_by_relevance(_docs(), request=request, ...)  # match real signature
    assert called["threshold"] == 0.4
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd /Users/holobiomicslab/git/Perspicacite-AI && python -m pytest tests/.../test_profound*.py -v -k "screen"`
Expected: FAIL — method/threshold ignored, always rerank with 0.0.

- [ ] **Step 4: Implement screening-knob routing**

In `_filter_documents_by_relevance`, read `request.screen_method` and `request.screen_threshold`. Compute `effective_threshold = request.screen_threshold if request.screen_threshold is not None else 0.0`. Dispatch:
  - `None` / `"rerank"` → `screen_papers_rerank(..., threshold=effective_threshold)`
  - `"bm25"` → `screen_papers(..., threshold=effective_threshold)`
  - `"llm"` → `screen_papers_llm(..., threshold=effective_threshold)` only if its required deps (e.g. llm client) are available in scope; otherwise fall back to rerank.
  - unknown → fall back to rerank.
Preserve the existing KB-doc preservation and top-N tail logic. Source each screening function's other args from what the method already has; do NOT invent unavailable arguments — fall back to rerank if a method's deps are missing.

- [ ] **Step 5: Run screening tests to verify pass**

Run: `python -m pytest tests/.../test_profound*.py -v -k "screen"`
Expected: PASS

- [ ] **Step 6: Write failing test (cycle reflect phase_progress)**

Add a test that runs profound's `execute_stream` (or the cycle entry point the existing tests drive) with a fake telemetry sink (a list whose `.append` records events), with `max_cycles >= 1`, and asserts the captured events include a `{"kind":"phase_progress","phase":"reflect","state":"running","cycle":0,...}` and a matching `"done"` event. Mirror how existing profound streaming tests set up the request, telemetry_sink, and minimal LLM/retriever stubs.

- [ ] **Step 7: Run test to verify it fails**

Run: `python -m pytest tests/.../test_profound*.py -v -k "reflect or phase"`
Expected: FAIL — no reflect phase emitted.

- [ ] **Step 8: Implement cycle phase_progress**

Inside the cycle loop, wrap the `_create_iteration_summary(...)` call:
```python
emit_phase(_phase_sink, phase="reflect", state="running", cycle=cycle)
summary = self._create_iteration_summary(...)   # existing call
emit_phase(_phase_sink, phase="reflect", state="done", cycle=cycle)
```
Use the same `_phase_sink` and null-guarding the file already uses for `emit_phase`. Do not alter existing phase emissions.

- [ ] **Step 9: Run profound tests + adjacent suite**

Run: `python -m pytest tests/.../test_profound*.py tests/rag/ -v`
Expected: PASS, no regressions.

- [ ] **Step 10: Commit**

```bash
cd /Users/holobiomicslab/git/Perspicacite-AI
git add src/perspicacite/rag/modes/profound.py tests/
git commit -m "feat(profound): consume screen_method/screen_threshold and emit reflect phase_progress"
```

---

### Task 3: ASB search_related_papers via search_literature

**Files:**
- Modify: `src/agentic_science_builder/perspicacite_client.py` (`MCPPerspicaciteClient.search_related_papers` ~lines 488–515; update docstring)
- Test: `tests/test_perspicacite_client.py` (existing direct test of this method)
- Reference (read-only): Perspicacite-AI `src/perspicacite/mcp/server.py` `search_literature` tool signature to confirm arg names + response shape

- [ ] **Step 1: Read current code + server tool**

Read `MCPPerspicaciteClient.search_related_papers` and a working sibling (`search_by_passage`) for the MCP-call pattern in `perspicacite_client.py`. Read the `search_literature` tool in `/Users/holobiomicslab/git/Perspicacite-AI/src/perspicacite/mcp/server.py` to confirm the request arg names (query + result-count arg) and the response shape (key holding the paper list, and each paper's doi/title/year/score field names).

- [ ] **Step 2: Write the failing test**

In `tests/test_perspicacite_client.py`, find the existing `search_related_papers` test. Update/add a test that stubs `_MCPSession.call_tool` to return a `search_literature`-shaped payload (a list or dict-with-results of paper rows) and asserts:
  - `call_tool` was called with `"search_literature"` and args containing the query and the result-count (`k`).
  - The method returns `RelatedPaper` objects with the mapped doi/title/year/score.
  - A `RuntimeError` from `call_tool` yields `[]`.
  - A malformed row (no doi, no title) is skipped.

Match the existing test's stubbing style (how it patches `_sess()/_MCPSession`).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /Users/holobiomicslab/git/AgenticScienceBuilder && python -m unittest tests.test_perspicacite_client -v`
Expected: FAIL — current method calls `"search_related_papers"` tool, returns `[]`.

- [ ] **Step 4: Implement search_literature-backed method**

Reimplement `search_related_papers`:
```python
def search_related_papers(self, query: str, *, k: int = 5) -> list[RelatedPaper]:
    """Related papers for a free-text query, via Perspicacité's search_literature tool."""
    try:
        raw = self._sess().call_tool(
            "search_literature", {"query": query, "max_results": k}
        )
    except RuntimeError:
        return []
    rows = (
        raw.get("results") or raw.get("papers")
        if isinstance(raw, dict)
        else raw if isinstance(raw, list) else []
    )
    out: list[RelatedPaper] = []
    for r in rows or []:
        doi = str(r.get("doi", "") or "")
        title = str(r.get("title", "") or "")
        if not doi and not title:
            continue
        try:
            out.append(RelatedPaper(
                doi=doi, title=title,
                year=r.get("year"),
                score=float(r.get("score", 0.0) or 0.0),
            ))
        except (TypeError, ValueError):
            continue
    return out
```
Adjust the request arg name (`max_results` vs other) and the response key (`results`/`papers`/bare list) to match what Step 1 found in the real `search_literature` tool.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m unittest tests.test_perspicacite_client -v`
Expected: PASS

- [ ] **Step 6: Run related ASB suites**

Run: `cd /Users/holobiomicslab/git/AgenticScienceBuilder && python -m unittest tests.test_skill_pack_v3_provenance tests.test_library_mcp_enrichment tests.test_perspicacite_enrichment -v`
Expected: PASS (these mock the client; mock signature unchanged so they should be green).

- [ ] **Step 7: Commit**

```bash
cd /Users/holobiomicslab/git/AgenticScienceBuilder
git add src/agentic_science_builder/perspicacite_client.py tests/test_perspicacite_client.py
git commit -m "feat(perspicacite-client): implement search_related_papers via search_literature"
```

---

## Final verification (after all tasks)

- Perspicacite-AI: `cd /Users/holobiomicslab/git/Perspicacite-AI && python -m pytest -q` — expect prior baseline (1874 passed/1 skipped) plus new tests, all green.
- ASB: `cd /Users/holobiomicslab/git/AgenticScienceBuilder && python -m unittest discover -s tests -v` — all green.
- Confirm no files under `docs/superpowers/` are staged or committed in either repo.
