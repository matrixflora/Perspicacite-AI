# Master Execution Plan — Scriptorium MCP Findings + ASB Bundle Ingest

> **For agentic workers:** REQUIRED SUB-SKILL: Use **superpowers:subagent-driven-development** to execute this plan. Each phase has tasks; dispatch one implementer subagent per task, two-stage review (spec compliance → code quality) after each task, mark complete, move on. Do NOT pause for user check-ins between tasks. Roll through all phases continuously. Phase boundaries are organisational only — there is no gate to clear before moving on.

**Goal:** Land two streams of work in one continuous subagent run:

1. **Phase A + B (Scriptorium MCP findings)** — fix concrete bugs and UX issues a real downstream client (Scriptorium v0.13) hit integrating against Perspicacité v3.2.4's MCP server.
2. **Phase C + D (ASB Bundle Ingest)** — ship the ASB-output → KB ingest path per [`docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md`](../specs/2026-05-15-asb-bundle-ingest-design.md), with the schema-drift handling from the 2026-05-16 ASB run baked in.

Total: ~26 tasks across 5 phases. Estimated 15-30 hours of subagent wall-time with TDD + two-stage review. Tasks are self-contained and ordered to maintain a green main branch throughout.

**Tech Stack:** Python 3.11+, pydantic v2, httpx, FastAPI, MCP server framework, Click CLI, Chroma vector store, the existing `Paper`/`PaperSource` model, `TypedEmbeddingProvider`, `DynamicKnowledgeBase`.

---

## Standing notes for the implementer + driver

- **PYTHONPATH=src** when running pytest in this worktree. Editable install points at the main repo, not this worktree's `src/`. Without it, you'll silently test stale code.
- **Per-task commits directly to the worktree branch.** Don't push. The user fast-forwards `main` at end of session.
- **TDD discipline:** every task writes the failing test first, runs to confirm failure, writes minimal impl, runs to confirm pass, then commits. The driver enforces this via the implementer subagent prompt.
- **Continuous execution:** do not stop between tasks unless `BLOCKED`. If `BLOCKED`, escalate; otherwise dispatch the next implementer.
- **Model selection:** use the cheapest capable model. Phase A bug fixes are mechanical (haiku/sonnet); Phase B + C + D have integration breadth (sonnet); design judgement / debugging (opus) only on failure.
- **Heredoc commit messages** end with `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>`.

---

## Phase A — Scriptorium MCP critical fixes

Five concrete bugs / docs gaps in the MCP server. Small, isolated, high-value. A real downstream client (Scriptorium v0.11–v0.13) hit each of these integrating against Perspicacité v3.2.4. Each task is 30-90 minutes of implementer work.

### Task A1: Fix `PaperSource` enum repr leaking into JSON

**Bug:** `mcp/server.py:220` serialises `Paper.source` with `str(p.source)`, which produces `"PaperSource.SCILEX"` (Python enum repr) instead of `"scilex"` (the JSON-friendly value). Downstream clients that match on the lowercase value break.

**Files:**
- Modify: `src/perspicacite/mcp/server.py:220` (and any other `str(p.source)` sites — grep first)
- Test: `tests/unit/test_mcp_paper_source_serialization.py` (new)

- [ ] **Step 1: Find every `str(p.source)` site**

Run: `grep -n 'str(p.source)\|str(paper.source)' src/perspicacite/mcp/server.py`
Expected: at least line 220. Note all sites — fix each.

- [ ] **Step 2: Write the failing serialization test**

```python
# tests/unit/test_mcp_paper_source_serialization.py
"""After the 2026-05-15 Scriptorium-integration audit, the MCP
server must serialize PaperSource as its JSON-friendly .value
('scilex', 'openalex', ...) — not Python's enum repr
('PaperSource.SCILEX'). Downstream clients depend on the lowercase
value to dispatch."""
import inspect

from perspicacite.mcp import server


def test_mcp_server_does_not_emit_enum_repr():
    src = inspect.getsource(server)
    # The repr-style serialization is the bug:
    assert "str(p.source)" not in src, (
        "mcp/server.py must not serialize PaperSource via str() — use .value"
    )
    assert "str(paper.source)" not in src, (
        "mcp/server.py must not serialize PaperSource via str() — use .value"
    )


def test_paper_source_value_is_lowercase_snake():
    """Sanity: the enum .value is the JSON-friendly form."""
    from perspicacite.models.papers import PaperSource
    assert PaperSource.SCILEX.value == "scilex"
    assert PaperSource.OPENALEX.value == "openalex"
```

- [ ] **Step 3: Run test to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_paper_source_serialization.py -v`
Expected: FAIL on `test_mcp_server_does_not_emit_enum_repr` (the string is in `server.py`).

- [ ] **Step 4: Fix the call site(s)**

In `src/perspicacite/mcp/server.py:220` (and any other matching grep hits):

```python
# before
"source": str(p.source) if p.source else None,
# after
"source": p.source.value if p.source else None,
```

- [ ] **Step 5: Run the test + a quick smoke against search_literature**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_paper_source_serialization.py -v`
Expected: PASS.

If the `search_literature` MCP path has end-to-end test coverage, run it too (`grep -rln 'test.*search_literature' tests/`).

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_paper_source_serialization.py
git commit -m "fix(mcp): serialize PaperSource as .value, not enum repr"
```

---

### Task A2: Standardize MCP envelope key (`success` vs `ok`) + document

**Bug:** `mcp/server.py:131` emits `"success": True` from `_json_ok`. Downstream clients hardcoding `ok` (an earlier convention) break. The fix is to (a) pick one key canonically going forward and (b) document the contract so this doesn't drift again.

**Decision (reasonable call):** keep `"success"` (current behaviour — don't break existing clients), add `"ok"` as a synonym for one minor cycle (cheap insurance), document the contract in [`docs/MCP.md`](../../MCP.md) (or create the file). Plan for `"ok"` removal in a future release with deprecation note.

**Files:**
- Modify: `src/perspicacite/mcp/server.py:129-143` (`_json_ok` / `_json_error`)
- Create: `docs/MCP.md` (new doc; canonical envelope contract)
- Test: `tests/unit/test_mcp_envelope.py` (new)

- [ ] **Step 1: Write the failing envelope test**

```python
# tests/unit/test_mcp_envelope.py
"""Pin the MCP JSON envelope shape. Both 'success' and 'ok' keys
are emitted for one minor cycle to ease the Scriptorium-v0.13
downstream client migration; 'ok' is the deprecated alias."""
import json

from perspicacite.mcp.server import _json_ok, _json_error


def test_json_ok_emits_both_success_and_ok():
    payload = json.loads(_json_ok({"x": 1}))
    assert payload["success"] is True
    assert payload["ok"] is True
    assert payload["x"] == 1


def test_json_error_emits_both_success_and_ok_false():
    payload = json.loads(_json_error("boom"))
    assert payload["success"] is False
    assert payload["ok"] is False
    assert payload["error"] == "boom"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_envelope.py -v`
Expected: FAIL — `ok` key not in current output.

- [ ] **Step 3: Update the helpers**

In `src/perspicacite/mcp/server.py:129-143`:

```python
def _json_ok(data: dict[str, Any]) -> str:
    """Emit a successful MCP envelope.

    Carries both ``success: true`` (canonical, will remain) and
    ``ok: true`` (deprecated alias for backwards compat with
    pre-v3.x downstream clients). Plan to drop ``ok`` after the
    Scriptorium-v0.13 migration completes — see docs/MCP.md.
    """
    return json.dumps(
        {"success": True, "ok": True, **data},
        ensure_ascii=False,
        default=str,
    )


def _json_error(message: str, **extra: Any) -> str:
    return json.dumps(
        {"success": False, "ok": False, "error": message, **extra},
        default=str,
    )
```

- [ ] **Step 4: Create the canonical envelope doc**

```markdown
# docs/MCP.md

# Perspicacité MCP Server — Wire Contract

The MCP server (FastMCP-based, mounted at `/mcp`) returns every tool
result as a JSON string. The envelope is stable across all tools.

## Envelope shape

Success:
```json
{"success": true, "ok": true, "<tool-specific keys>": "..."}
```
- `success` (canonical) — always present on success.
- `ok` (deprecated alias) — present for one minor cycle. Downstream
  clients should migrate to `success`. Will be removed after v3.x.

Error:
```json
{"success": false, "ok": false, "error": "<human-readable message>"}
```

## Why both keys (2026-05-15)

The Scriptorium downstream client integration found that v3.2.4 emits
`success` but earlier code used `ok`. We emit both for one cycle so
existing clients don't break during the migration.

## Latency expectations

- `search_literature` with the default 3-backend fan-out: budget
  **15-50s** per call when titles match many candidates. The
  default httpx timeout in MCP clients should be at least **60s**.
- `search_knowledge_base`: typically <1s.
- `generate_report`: 30-120s depending on KB size + LLM speed.

## Authentication

There is no auth on the MCP endpoint by default. Run behind a
reverse proxy or expose only on `localhost` in production.
```

- [ ] **Step 5: Run the test + verify**

Run: `PYTHONPATH=src pytest tests/unit/test_mcp_envelope.py -v`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/mcp/server.py tests/unit/test_mcp_envelope.py docs/MCP.md
git commit -m "fix(mcp): emit both success+ok envelope keys for one cycle; document contract"
```

---

### Task A3: Diagnose + fix `search_literature` default-databases regression

**Bug:** When `databases` is omitted, queries return `total_results: 0` for titles that resolve fine when `databases=["semantic_scholar"]` is set explicitly. The default in `mcp/server.py:207` is `["semantic_scholar", "openalex", "pubmed"]`. Either one of the default backends is failing silently and the aggregator returns its (zero) result, or the threading itself is broken.

**Files (investigation phase):**
- Read: `src/perspicacite/mcp/server.py:155-235` (`search_literature` tool)
- Read: `src/perspicacite/search/scilex_adapter.py` (the adapter)
- Read: any per-backend module SciLEx wraps (openalex, pubmed)

**Files (fix phase):**
- Modify: `src/perspicacite/search/scilex_adapter.py` or per-backend modules
- Test: `tests/integration/test_search_literature_default_databases.py` (new)

- [ ] **Step 1: Reproduce the bug**

Run a smoke test that calls `SciLExAdapter.search` with the default `apis=["semantic_scholar", "openalex", "pubmed"]` against a known-good title (e.g., "Attention Is All You Need" or "AgentSquare"). Confirm zero results. Then re-run with `apis=["semantic_scholar"]` only; confirm non-zero.

```python
# Throw-away repro script — run with PYTHONPATH=src python ...
import asyncio
from perspicacite.search.scilex_adapter import SciLExAdapter

async def main():
    adapter = SciLExAdapter()
    if not adapter.available:
        print("SciLEx not installed; install with .[scilex]")
        return
    for apis in [None, ["semantic_scholar"], ["openalex"], ["pubmed"]]:
        papers = await adapter.search(
            query="AgentSquare Automatic LLM Agent Search",
            max_results=5,
            apis=apis or ["semantic_scholar", "openalex", "pubmed"],
        )
        print(f"apis={apis}: {len(papers)} results")

asyncio.run(main())
```

Note which backend returns 0 (or errors silently). That's the root cause.

- [ ] **Step 2: Write the failing test**

Once the failing backend is identified, write a pin test. (Live network test — gate with `@pytest.mark.skipif(no_internet)`.)

```python
# tests/integration/test_search_literature_default_databases.py
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("PERSPICACITE_LIVE_SEARCH") != "1",
    reason="Set PERSPICACITE_LIVE_SEARCH=1 to hit external APIs",
)


@pytest.mark.asyncio
async def test_search_literature_default_databases_returns_results():
    """When `databases` is omitted, search_literature must not return
    zero for queries that succeed against a single backend (i.e., a
    silent backend failure must not poison the aggregate)."""
    from perspicacite.search.scilex_adapter import SciLExAdapter

    adapter = SciLExAdapter()
    if not adapter.available:
        pytest.skip("SciLEx not installed")
    # Use a paper that S2 definitely knows
    papers = await adapter.search(
        query="Attention Is All You Need",
        max_results=5,
        # Use the same default as the MCP tool path
        apis=["semantic_scholar", "openalex", "pubmed"],
    )
    assert len(papers) > 0, "Default-databases search returned 0 — silent backend?"


@pytest.mark.asyncio
async def test_search_literature_per_backend_smoke():
    """Each default backend should return >0 for an unambiguous title."""
    from perspicacite.search.scilex_adapter import SciLExAdapter

    adapter = SciLExAdapter()
    if not adapter.available:
        pytest.skip("SciLEx not installed")
    for api in ["semantic_scholar", "openalex", "pubmed"]:
        papers = await adapter.search(
            query="CRISPR Cas9 genome editing",
            max_results=3,
            apis=[api],
        )
        # Backend-specific zero is acceptable for some queries, but
        # this one is broad enough that any working backend returns >0.
        # If a backend returns 0, the test points at the regression.
        assert len(papers) > 0, f"Backend {api} returned 0 — investigate"
```

- [ ] **Step 3: Run + verify failure**

Run: `PYTHONPATH=src PERSPICACITE_LIVE_SEARCH=1 pytest tests/integration/test_search_literature_default_databases.py -v`
Expected: at least one assertion fails (matching the bug report).

- [ ] **Step 4: Fix root cause**

The fix depends on what step 1 surfaced. Likely candidates:
- One backend module raises an exception that's caught silently — make it propagate or log loudly
- The aggregator dedupes incorrectly (e.g., empty list + non-empty list → empty)
- A default backend (e.g., arxiv) was never initialised but is listed in the default

Implement the minimal fix. Add an INFO log per backend (`backend=X, results=N`) so silent failures are visible going forward.

- [ ] **Step 5: Verify pass**

Run: `PYTHONPATH=src PERSPICACITE_LIVE_SEARCH=1 pytest tests/integration/test_search_literature_default_databases.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/search/ tests/integration/test_search_literature_default_databases.py
git commit -m "fix(search): default-databases fan-out — surface per-backend failures"
```

---

### Task A4: Fix `/api/chat` REST endpoint field name

**Bug:** OpenAPI schema lists `/api/chat` accepting `{"message": ...}` but `ChatRequest.query` (line 51) requires `query`. POST `{"message": "..."}` returns 422.

**Reasonable call:** accept both. Rename is risky (breaks current clients); add `message` as an alias via a pydantic validator. Update the OpenAPI doc to reflect both.

**Files:**
- Modify: `src/perspicacite/web/routers/chat.py:48-65` (`ChatRequest`)
- Test: `tests/unit/test_chat_request_message_alias.py` (new)

- [ ] **Step 1: Write the failing alias test**

```python
# tests/unit/test_chat_request_message_alias.py
"""POST /api/chat must accept either {"query": ...} (canonical) or
{"message": ...} (Scriptorium-v0.13-compatible alias). Both should
populate ChatRequest.query."""
import pytest


def test_chat_request_accepts_query():
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(query="hello")
    assert req.query == "hello"


def test_chat_request_accepts_message_alias():
    """Backward-compat for Scriptorium-v0.13 and clients reading
    the legacy OpenAPI schema field name."""
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(message="hello")  # type: ignore[call-arg]
    assert req.query == "hello"


def test_chat_request_prefers_query_when_both_supplied():
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(query="canonical", message="alias")  # type: ignore[call-arg]
    assert req.query == "canonical"
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_chat_request_message_alias.py -v`
Expected: FAIL — `message` not accepted.

- [ ] **Step 3: Add the alias on `ChatRequest`**

In `src/perspicacite/web/routers/chat.py`:

```python
from pydantic import BaseModel, Field, model_validator


class ChatRequest(BaseModel):
    """Incoming chat request. Accepts ``query`` (canonical) or
    ``message`` (Scriptorium-compat alias)."""

    query: str = Field(..., description="Current research question")
    # ...other existing fields...

    @model_validator(mode="before")
    @classmethod
    def _accept_message_alias(cls, data):
        """Backward-compat with the legacy OpenAPI schema name ``message``.
        If ``query`` is absent but ``message`` is supplied, promote it.
        ``query`` always wins when both are present."""
        if isinstance(data, dict) and "query" not in data and "message" in data:
            data = {**data, "query": data["message"]}
        return data
```

- [ ] **Step 4: Verify pass**

Run: `PYTHONPATH=src pytest tests/unit/test_chat_request_message_alias.py -v`
Expected: 3 passed.

- [ ] **Step 5: Smoke against the running server (optional, if dev server is up)**

```bash
curl -s -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"hi"}' | head -c 200
# Expected: SSE stream, not 422
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/web/routers/chat.py tests/unit/test_chat_request_message_alias.py
git commit -m "fix(api): /api/chat accepts {message: ...} as a {query: ...} alias"
```

---

### Task A5: README quick-start mention of MCP serving

**Gap:** README "Quick Start" doesn't say that `perspicacite serve` exposes the MCP server on `/mcp` on the same port. Scriptorium had to grep `cli.py` to confirm.

**Files:**
- Modify: `README.md` (Quick Start section)

- [ ] **Step 1: Locate the Quick Start section**

Run: `grep -n 'Quick Start\|## Get' README.md | head -5`

- [ ] **Step 2: Add a one-paragraph note in the Quick Start**

Just after the `perspicacite serve` command snippet, add:

```markdown
The dev server also hosts the MCP server at `http://localhost:8000/mcp`
(streamable HTTP). MCP clients connect there to call tools like
`search_knowledge_base`, `generate_report`, and `ingest_asb_run`.
See [docs/MCP.md](docs/MCP.md) for the envelope contract and latency
expectations.
```

(If `docs/MCP.md` doesn't exist yet because Task A2 hasn't landed, the link is a forward reference — Task A2 creates the file.)

- [ ] **Step 3: Smoke-check the README renders sensibly**

Run: `head -80 README.md` — confirm the addition reads cleanly in context.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs(readme): note that perspicacite serve hosts MCP at /mcp"
```

---

## Phase B — Scriptorium UX / perf improvements

Larger items that surface real product wins for downstream clients. Each is 1-3 hours of implementer work.

### Task B1: Title-normalisation retry for `search_literature`

**Bug:** Title matching is strict. "AgentSquare: Automatic LLM Agent Search in Modular Design Space" returns 0; the same query without the post-colon subtitle returns N. The existing `--rephrase N` flag goes the other direction (expand queries via LLM) — what's needed is a cheaper normalize-then-retry on 0-result queries.

**Approach:** in `SciLExAdapter.search` (or a wrapping helper), when the first pass returns 0 and the query looks like a title (contains `:` or parenthetical), retry once with a normalised form. Track in result metadata whether the retry was used.

**Files:**
- Modify: `src/perspicacite/search/scilex_adapter.py` (or a wrapping helper module)
- Test: `tests/unit/test_search_title_normalize.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_search_title_normalize.py
"""When a title-like query returns 0 hits, the adapter should
retry once with a normalised form (drop post-colon subtitle,
drop parentheticals)."""
from unittest.mock import AsyncMock, patch

import pytest


def test_normalize_title_strips_subtitle_after_colon():
    from perspicacite.search.title_normalize import normalize_title
    assert normalize_title(
        "AgentSquare: Automatic LLM Agent Search in Modular Design Space"
    ) == "AgentSquare"


def test_normalize_title_strips_parentheticals():
    from perspicacite.search.title_normalize import normalize_title
    assert normalize_title(
        "Promptbreeder (v2): Self-Referential Self-Improvement"
    ) == "Promptbreeder"


def test_normalize_title_returns_input_when_already_short():
    from perspicacite.search.title_normalize import normalize_title
    assert normalize_title("Attention") == "Attention"


@pytest.mark.asyncio
async def test_search_with_normalize_retries_once_on_zero():
    from perspicacite.search.scilex_adapter import SciLExAdapter

    calls: list[str] = []

    async def fake_search(*, query, **kw):
        calls.append(query)
        # First call returns []; second call (normalised) returns 1 paper
        if len(calls) == 1:
            return []
        from perspicacite.models.papers import Paper, PaperSource
        return [Paper(id="x", title="AgentSquare", source=PaperSource.SCILEX)]

    adapter = SciLExAdapter()
    with patch.object(adapter, "_raw_search", new=fake_search):
        out = await adapter.search(
            query="AgentSquare: Automatic LLM Agent Search in Modular Design Space",
            max_results=5,
            apis=["semantic_scholar"],
        )
    assert len(out) == 1
    assert len(calls) == 2  # original then normalised
    assert calls[0].startswith("AgentSquare:")
    assert calls[1] == "AgentSquare"


@pytest.mark.asyncio
async def test_search_with_normalize_no_retry_when_first_pass_succeeds():
    from perspicacite.search.scilex_adapter import SciLExAdapter
    from perspicacite.models.papers import Paper, PaperSource

    calls: list[str] = []

    async def fake_search(*, query, **kw):
        calls.append(query)
        return [Paper(id="x", title=query, source=PaperSource.SCILEX)]

    adapter = SciLExAdapter()
    with patch.object(adapter, "_raw_search", new=fake_search):
        out = await adapter.search(query="Attention Is All You Need",
                                   max_results=5, apis=["semantic_scholar"])
    assert len(out) == 1
    assert len(calls) == 1  # no retry needed
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_search_title_normalize.py -v`
Expected: ImportError on `title_normalize`.

- [ ] **Step 3: Implement the normaliser + retry**

```python
# src/perspicacite/search/title_normalize.py
"""Cheap title normalisation for search-retry on 0-result queries.

Drops everything after the first colon ("Title: subtitle" → "Title")
and removes parentheticals. Cheap heuristic, not LLM-driven —
fast retry alternative to --rephrase."""
from __future__ import annotations

import re

_PAREN_RE = re.compile(r"\s*\([^)]*\)")


def normalize_title(query: str) -> str:
    """Return a stripped form suitable for a 0-result retry."""
    s = _PAREN_RE.sub("", query).strip()
    if ":" in s:
        s = s.split(":", 1)[0].strip()
    return s or query
```

In `src/perspicacite/search/scilex_adapter.py`:

```python
# Inside SciLExAdapter
async def search(self, *, query, max_results, apis, **kw):
    # First pass
    results = await self._raw_search(query=query, max_results=max_results,
                                     apis=apis, **kw)
    if results:
        return results
    # Zero-result retry path — only when the query looks like a title
    from perspicacite.search.title_normalize import normalize_title
    normalised = normalize_title(query)
    if normalised != query and len(normalised) >= 4:
        results = await self._raw_search(
            query=normalised, max_results=max_results, apis=apis, **kw,
        )
        for p in results:
            # Annotate so downstream knows
            p.metadata["search_normalized_from"] = query
    return results
```

(If `_raw_search` doesn't exist yet, refactor the existing `search` body into `_raw_search` and have the public `search` wrap with the retry logic. The refactor is small but make sure existing tests still pass.)

- [ ] **Step 4: Run + verify**

Run: `PYTHONPATH=src pytest tests/unit/test_search_title_normalize.py tests/unit/test_*search* -v`
Expected: all pass (including any pre-existing search-adapter tests).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/title_normalize.py src/perspicacite/search/scilex_adapter.py tests/unit/test_search_title_normalize.py
git commit -m "feat(search): normalise-then-retry on zero-result title queries"
```

---

### Task B2: Parallelise per-backend search fan-out

**Bug / perf:** `search_literature` with the default 3-backend fan-out takes 15-50s (serial). Parallelising with `asyncio.gather` cuts wall time to the slowest single backend (~5-15s).

**Approach:** in `SciLExAdapter._raw_search` (post-Task B1 refactor), gather per-backend calls instead of awaiting each in sequence. Capture per-backend errors so one slow/failed backend doesn't poison the rest.

**Files:**
- Modify: `src/perspicacite/search/scilex_adapter.py`
- Test: `tests/unit/test_scilex_parallel.py` (new)

- [ ] **Step 1: Write the failing parallel test**

```python
# tests/unit/test_scilex_parallel.py
"""SciLExAdapter should fan out per-backend calls concurrently
(asyncio.gather), not serially. Test by measuring wall time
against simulated 1-sec-per-backend latency: 3 backends serial
~3.0s, parallel ~1.0s."""
import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_per_backend_search_runs_in_parallel(monkeypatch):
    from perspicacite.search.scilex_adapter import SciLExAdapter

    call_times: list[float] = []

    async def slow_backend(*, query, max_results, **kw):
        call_times.append(time.monotonic())
        await asyncio.sleep(0.5)
        from perspicacite.models.papers import Paper, PaperSource
        return [Paper(id=f"id-{kw.get('api')}", title=query, source=PaperSource.SCILEX)]

    adapter = SciLExAdapter()
    # Patch the per-backend call (name varies; pick whatever the
    # adapter uses internally — e.g., _query_backend or _ss_search).
    with patch.object(adapter, "_query_backend", new=slow_backend):
        t0 = time.monotonic()
        results = await adapter._raw_search(
            query="x", max_results=3,
            apis=["semantic_scholar", "openalex", "pubmed"],
        )
        t1 = time.monotonic()

    # If parallel: ~0.5s total. If serial: ~1.5s total.
    assert (t1 - t0) < 1.0, f"Fan-out appears serial (took {t1-t0:.2f}s)"
    assert len(results) == 3
    # Spread of call_times is small (parallel start)
    assert max(call_times) - min(call_times) < 0.1


@pytest.mark.asyncio
async def test_per_backend_failure_does_not_poison_others():
    """If one backend raises, the others still return their results."""
    from perspicacite.search.scilex_adapter import SciLExAdapter
    from perspicacite.models.papers import Paper, PaperSource

    async def flaky_backend(*, query, max_results, api, **kw):
        if api == "openalex":
            raise RuntimeError("openalex flaked")
        return [Paper(id=f"id-{api}", title=query, source=PaperSource.SCILEX)]

    adapter = SciLExAdapter()
    with patch.object(adapter, "_query_backend", new=flaky_backend):
        results = await adapter._raw_search(
            query="x", max_results=3,
            apis=["semantic_scholar", "openalex", "pubmed"],
        )
    # Two backends succeeded
    assert len(results) == 2
    api_ids = {p.id for p in results}
    assert "id-openalex" not in api_ids
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_scilex_parallel.py -v`
Expected: serial timing fails or `_query_backend` doesn't exist.

- [ ] **Step 3: Refactor `_raw_search` to use `asyncio.gather` with per-backend isolation**

```python
# In src/perspicacite/search/scilex_adapter.py — sketch

async def _raw_search(self, *, query, max_results, apis, **kw):
    """Fan out per-backend calls concurrently. One backend failing
    doesn't poison the others."""
    coros = [
        self._query_backend(query=query, max_results=max_results, api=api, **kw)
        for api in apis
    ]
    results_per_backend = await asyncio.gather(*coros, return_exceptions=True)
    merged: list[Paper] = []
    for api, res in zip(apis, results_per_backend):
        if isinstance(res, Exception):
            logger.warning("scilex_backend_failed", backend=api, error=str(res))
            continue
        merged.extend(res)
    return self._dedupe(merged)  # whatever dedup exists already
```

(Implementer: if `_query_backend` doesn't exist as a clean per-backend hook, extract one from whatever per-backend logic exists today — the refactor itself is half the work. Capture the public `search` signature unchanged.)

- [ ] **Step 4: Run + verify**

Run: `PYTHONPATH=src pytest tests/unit/test_scilex_parallel.py tests/unit/test_*search* -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/search/scilex_adapter.py tests/unit/test_scilex_parallel.py
git commit -m "perf(search): parallelise per-backend fan-out with asyncio.gather"
```

---

### Task B3: Document MCP tool latency in tool descriptions

**Gap:** `docs/MCP.md` (Task A2) covers latency at the doc level. The MCP tool *descriptions* themselves (visible to LLM clients via `tools/list`) don't mention latency. Add a one-liner in the docstring of each slow tool so calling LLMs know to budget.

**Files:**
- Modify: `src/perspicacite/mcp/server.py` — extend the docstrings of `search_literature`, `generate_report`, any other tool with multi-second latency.

- [ ] **Step 1: Find each multi-second tool**

Run: `grep -n '@mcp.tool()' src/perspicacite/mcp/server.py | head -20`
For each, decide if it's <1s, 1-15s, or >15s, based on what it does (LLM calls, network fan-out, KB-wide retrieval).

- [ ] **Step 2: Add the latency note to docstrings**

Pattern:

```python
@mcp.tool()
async def search_literature(
    query: str,
    ...
) -> str:
    """
    Search academic databases for scientific papers matching a query.

    **Latency:** 15-50s on the default 3-backend fan-out; budget at
    least 60s in the calling client's HTTP timeout. Single-backend
    queries are 5-15s.

    Args:
        ...
    """
```

Apply to: `search_literature`, `search_knowledge_base` (if >1s), `generate_report`, `add_dois_to_kb`, any DOI/PDF fetcher.

- [ ] **Step 3: No new test required — the docstrings are surfaced verbatim in `tools/list`. Smoke-check manually if a server is running:**

```bash
# Not a CI test; manual verification
curl -s -X POST http://localhost:8000/mcp \
     -H 'Content-Type: application/json' \
     -d '{"method":"tools/list","jsonrpc":"2.0","id":1}' \
     | python3 -m json.tool | grep -A 2 'Latency'
```

- [ ] **Step 4: Commit**

```bash
git add src/perspicacite/mcp/server.py
git commit -m "docs(mcp): inline latency expectations in slow tool docstrings"
```

---

### Task B4: Raw-LLM endpoint `/api/llm/proxy`

**Feature:** Scriptorium wants to use Perspicacité as an LLM gateway (the API keys are already configured). A new endpoint `/api/llm/proxy` accepts a prompt + model, routes through the configured provider with stage-tiering rules honoured, and returns a streaming response. **Strict scope:** no RAG, no KB awareness, no retrieval — pure credentials-routing benefit.

**Files:**
- Create: `src/perspicacite/web/routers/llm_proxy.py`
- Modify: `src/perspicacite/web/main.py` (or wherever routers are mounted) — include the new router
- Test: `tests/unit/test_llm_proxy_endpoint.py` (new)

- [ ] **Step 1: Write the failing endpoint test**

```python
# tests/unit/test_llm_proxy_endpoint.py
"""POST /api/llm/proxy proxies a prompt through the configured LLM
provider with no RAG/KB awareness. Honours stage-tiering rules.
Used by external clients (e.g. Scriptorium) that want Perspicacité
to be their LLM gateway."""
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


def _client():
    from perspicacite.web.main import app
    return TestClient(app)


def test_llm_proxy_returns_streaming_text():
    with patch(
        "perspicacite.web.routers.llm_proxy._call_llm_streaming",
        new=AsyncMock(return_value=iter(["hel", "lo"])),
    ):
        client = _client()
        with client.stream(
            "POST", "/api/llm/proxy",
            json={"prompt": "say hi", "model": "claude-haiku-4-5"},
        ) as r:
            r.raise_for_status()
            chunks = list(r.iter_text())
    body = "".join(chunks)
    assert "hello" in body


def test_llm_proxy_validates_required_fields():
    client = _client()
    r = client.post("/api/llm/proxy", json={})
    assert r.status_code == 422


def test_llm_proxy_does_not_retrieve_or_touch_kb():
    """Smoke: the proxy module must not import KB / retrieval modules."""
    import inspect
    from perspicacite.web.routers import llm_proxy
    src = inspect.getsource(llm_proxy)
    assert "DynamicKnowledgeBase" not in src
    assert "auto_route_kbs" not in src
    assert "retrieve" not in src.lower() or "no retrieval" in src.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `PYTHONPATH=src pytest tests/unit/test_llm_proxy_endpoint.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the endpoint**

```python
# src/perspicacite/web/routers/llm_proxy.py
"""Raw-LLM proxy endpoint.

Lets external clients (e.g. Scriptorium) use Perspicacité as an
LLM gateway. No RAG, no KB awareness — just credentials routing
+ the existing stage-tiering rules.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/llm", tags=["llm-proxy"])


class LLMProxyRequest(BaseModel):
    prompt: str = Field(..., description="Raw prompt to send to the LLM")
    model: str | None = Field(
        default=None,
        description="Override model. Defaults to the config's default model.",
    )
    max_tokens: int = Field(default=2048, ge=1, le=32000)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    stage: str | None = Field(
        default=None,
        description="Stage-tiering hint ('fast', 'capable', 'reasoning'). "
                    "Routes to the matching model per the config tiering rules.",
    )


@router.post("/proxy")
async def llm_proxy(request: LLMProxyRequest) -> StreamingResponse:
    """Stream the model's response as text/plain chunks."""
    async def gen() -> AsyncIterator[bytes]:
        async for chunk in _call_llm_streaming(
            prompt=request.prompt,
            model=request.model,
            stage=request.stage,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
        ):
            yield chunk.encode("utf-8") if isinstance(chunk, str) else chunk

    return StreamingResponse(gen(), media_type="text/plain")


async def _call_llm_streaming(
    *,
    prompt: str,
    model: str | None,
    stage: str | None,
    max_tokens: int,
    temperature: float,
) -> AsyncIterator[str]:
    """Resolve the LLM client via the existing provider abstraction
    and stream chunks. No retrieval — pure pass-through."""
    from perspicacite.config import load_config
    from perspicacite.llm.client import get_llm_client  # actual name may differ

    config = load_config()
    resolved_model = model or config.llm.default_model
    if stage:
        resolved_model = config.llm.resolve_stage_model(stage) or resolved_model

    client = get_llm_client(model=resolved_model, config=config)
    async for chunk in client.stream(
        prompt=prompt, max_tokens=max_tokens, temperature=temperature,
    ):
        yield chunk
```

(Implementer: the exact LLM client name (`get_llm_client`, `LLMClient`, `llm_provider`, etc.) depends on what already exists. Grep `src/perspicacite/llm/` to find the right entry point. The key contract is: **no RAG, no KB**, just provider-credentials routing.)

In `src/perspicacite/web/main.py`:

```python
from perspicacite.web.routers import llm_proxy

app.include_router(llm_proxy.router)
```

- [ ] **Step 4: Verify pass**

Run: `PYTHONPATH=src pytest tests/unit/test_llm_proxy_endpoint.py -v`
Expected: 3 passed.

- [ ] **Step 5: Document in `docs/MCP.md`**

Append to `docs/MCP.md`:

```markdown
## REST: `/api/llm/proxy` (added 2026-05-15)

Pure LLM gateway. No RAG, no KB. Use this when you want
Perspicacité's configured API keys + stage-tiering rules but
don't want retrieval.

```bash
curl -X POST http://localhost:8000/api/llm/proxy \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is mass spectrometry?","model":"claude-haiku-4-5"}'
```
```

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/web/routers/llm_proxy.py src/perspicacite/web/main.py tests/unit/test_llm_proxy_endpoint.py docs/MCP.md
git commit -m "feat(api): /api/llm/proxy — raw-LLM gateway endpoint"
```

---

## Phase C — ASB schema refresh (precondition for Phase D)

Three small tasks that absorb the 2026-05-16 ASB schema drift into the existing ASB plan's parser and chunk-producer layers. These land before Phase D so the ASB-ingest tasks consume the right fixture and the right DAG format.

### Task C1: Refresh the ASB test fixture to use the 2026-05-16 run

The original ASB plan (Task 2) targets `audit_2026-05-15_pdf2/metlinkr_full`. The 2026-05-16 run (`audit_2026-05-16_workflow_validation/article_878_v4`) is more representative of current ASB output. Both should be supported, but the **primary** fixture switches to the newer run; a smaller secondary fixture covers the older schema.

**Files:**
- Create: `tests/fixtures/asb/article_878_v4_subset/` (primary — 2026-05-16 schema)
- Create: `tests/fixtures/asb/metlinkr_subset/` (secondary — 2026-05-15 schema; smaller, just for parser-tolerance regression)

- [ ] **Step 1: Verify both source paths exist**

Run: `ls ~/git/AgenticScienceBuilder/outputs/{audit_2026-05-15_pdf2/metlinkr_full,audit_2026-05-16_workflow_validation/article_878_v4}/cards/ | head -20`

- [ ] **Step 2: Copy the 2026-05-16 primary subset**

```bash
mkdir -p tests/fixtures/asb/article_878_v4_subset/{skills,cards,tools}

# Two skills (representative)
cp -r ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/skills/{mass-spectral-library-curation,chemical-structure-annotation-repair} tests/fixtures/asb/article_878_v4_subset/skills/

# Trimmed _index.json — only the two copied skills (edit manually)
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/skills/_index.json tests/fixtures/asb/article_878_v4_subset/skills/_index.json
# Edit to keep only the two skill entries

# Three cards
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/cards/task_00{1,2,3}.* tests/fixtures/asb/article_878_v4_subset/cards/

# Tool registry (small)
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/tools/* tests/fixtures/asb/article_878_v4_subset/tools/

# DAG
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-16_workflow_validation/article_878_v4/workflow_dag.json tests/fixtures/asb/article_878_v4_subset/workflow_dag.json
```

- [ ] **Step 3: Copy the 2026-05-15 secondary subset (regression coverage)**

```bash
mkdir -p tests/fixtures/asb/metlinkr_subset/{skills/cross-identifier-reconciliation,cards,tools}

cp -r ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/cross-identifier-reconciliation/* tests/fixtures/asb/metlinkr_subset/skills/cross-identifier-reconciliation/
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/skills/_index.json tests/fixtures/asb/metlinkr_subset/skills/_index.json
# Trim _index.json to one skill (cross-identifier-reconciliation)

cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/cards/task_00{1,2}.* tests/fixtures/asb/metlinkr_subset/cards/
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/tools/{metlinkr.json,r.json,_index.json} tests/fixtures/asb/metlinkr_subset/tools/
cp ~/git/AgenticScienceBuilder/outputs/audit_2026-05-15_pdf2/metlinkr_full/workflow_dag.json tests/fixtures/asb/metlinkr_subset/workflow_dag.json
```

- [ ] **Step 4: Confirm both fixtures load as JSON**

```bash
for f in tests/fixtures/asb/article_878_v4_subset/cards/*.json \
         tests/fixtures/asb/metlinkr_subset/cards/*.json \
         tests/fixtures/asb/*/workflow_dag.json; do
  python3 -c "import json; json.load(open('$f'))" || echo "FAIL: $f"
done
```

Expected: no FAIL output.

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/asb/
git commit -m "test(asb): refresh fixtures with 2026-05-16 + 2026-05-15 subsets"
```

---

### Task C2: Update DAG reader to handle both edge formats

The ASB plan's Task 5 (`pipeline/asb/dag.py`) parses edges as `list[tuple[str, str]]`. The 2026-05-16 schema uses `[{"from", "port", "to"}, ...]`. The reader must accept both and preserve port labels.

**Files (updated):**
- Modify: `src/perspicacite/pipeline/asb/dag.py` (will be created in Phase D Task 5; update its sketch in the ASB plan to handle both formats)
- Modify: `tests/unit/test_asb_dag.py` (will be created in Phase D Task 5; add a case for the new format)

**Driver note:** when Phase D Task 5 lands, the implementer subagent should follow the **updated** code/test in this section, not the original ASB plan's sketch. Quote the patch below in the implementer prompt.

- [ ] **Step 1: Updated `dag.py`** (replace the version in the ASB plan Task 5)

```python
# src/perspicacite/pipeline/asb/dag.py
"""Workflow DAG reader (workflow_dag.json).

Supports two on-disk edge formats:

  2026-05-15: edges as [[src, dst], ...]
  2026-05-16+: edges as [{"from": src, "port": label, "to": dst}, ...]

The internal Edge record carries an optional ``port`` label
preserving the data-flow name between tasks.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Edge:
    src: str
    dst: str
    port: str | None = None


@dataclass
class WorkflowDag:
    nodes: list[str] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def upstream(self, task_id: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == task_id]

    def downstream(self, task_id: str) -> list[str]:
        return [e.dst for e in self.edges if e.src == task_id]

    def edge_port(self, src: str, dst: str) -> str | None:
        for e in self.edges:
            if e.src == src and e.dst == dst:
                return e.port
        return None

    def to_dict(self) -> dict:
        return {
            "nodes": list(self.nodes),
            "edges": [
                {"from": e.src, "to": e.dst, "port": e.port}
                for e in self.edges
            ],
        }


def load_workflow_dag(run_dir: Path | str) -> WorkflowDag:
    """Return the workflow DAG. Missing or invalid file → empty DAG."""
    p = Path(run_dir) / "workflow_dag.json"
    if not p.exists():
        return WorkflowDag()
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError:
        return WorkflowDag()
    nodes = list(raw.get("nodes", []))
    edges_raw = raw.get("edges", [])
    edges: list[Edge] = []
    for e in edges_raw:
        if isinstance(e, dict):
            src = e.get("from")
            dst = e.get("to")
            port = e.get("port")
            if src and dst:
                edges.append(Edge(src=src, dst=dst, port=port))
        elif isinstance(e, (list, tuple)) and len(e) == 2:
            edges.append(Edge(src=e[0], dst=e[1], port=None))
    return WorkflowDag(nodes=nodes, edges=edges)
```

- [ ] **Step 2: Updated `test_asb_dag.py`** (add cases to the version planned in Phase D Task 5)

```python
# tests/unit/test_asb_dag.py — extend
def test_load_dag_handles_dict_edges_with_port_labels():
    """2026-05-16+ format: edges are dicts with from/port/to keys."""
    from perspicacite.pipeline.asb.dag import load_workflow_dag

    # The article_878_v4 fixture uses the dict format
    fixture = Path(__file__).parent.parent / "fixtures" / "asb" / "article_878_v4_subset"
    dag = load_workflow_dag(fixture)
    assert "task_001" in dag.nodes
    # task_001 → task_002 with port "cleaned_library"
    assert "task_002" in dag.downstream("task_001")
    assert dag.edge_port("task_001", "task_002") == "cleaned_library"


def test_load_dag_handles_list_pair_edges_legacy():
    """2026-05-15 format: edges are [src, dst] pairs (no port)."""
    from perspicacite.pipeline.asb.dag import load_workflow_dag

    fixture = Path(__file__).parent.parent / "fixtures" / "asb" / "metlinkr_subset"
    dag = load_workflow_dag(fixture)
    assert "task_001" in dag.nodes
    # Old format → port is None
    assert dag.edge_port("task_001", "task_002") is None
```

**Driver note:** when Phase D Task 5 executes, the implementer subagent must use the patches above instead of the original sketch. The Phase D plan and this master plan are jointly authoritative; on conflict, master plan wins.

- [ ] **No standalone commit for C2** — this task is documentation that updates Phase D Task 5. The actual commit happens when Phase D Task 5 lands.

---

### Task C3: Extend `ParsedCard` + `card_to_paper` for 2026-05-16 fields

Phase D Task 4 + Task 6 (card parser + chunk producer) must absorb the new fields: `executable: dict`, `task_inputs[]`, `task_outputs[]`, `task_objective`, `execution_profile`, `run_timeout_seconds`, `reproducibility_tier`, `expected_artifact_name`, `linked_result_ids`, `provenance_source`, `source_package`, `scenario_id`, `github_name` (replaces/fallback for `github`).

**Driver note:** when Phase D Task 4 + Task 6 execute, the implementer subagent must use the **extended** model + chunk producer below instead of the original sketches.

**Updated `ParsedCard` in `src/perspicacite/pipeline/asb/models.py`:**

```python
class ParsedCard(BaseModel):
    model_config = ConfigDict(extra="allow")

    # 2026-05-15 fields (already in the original sketch)
    task_id: str
    title: str = ""
    article_type: str | None = None
    domain: str | None = None
    primary_domain: str | None = None
    subdomains: list[str] = Field(default_factory=list)
    techniques: list[str] = Field(default_factory=list)
    subtask_categories: list[str] = Field(default_factory=list)
    crossref_doi: str | None = None
    tools_used: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    data_in: list[dict] = Field(default_factory=list)
    data_out: list[dict] = Field(default_factory=list)
    expected_outputs: list[str] = Field(default_factory=list)
    landmark_outputs: list[str] = Field(default_factory=list)
    parameters: list[dict] = Field(default_factory=list)
    domain_knowledge: list[str] = Field(default_factory=list)
    evaluation_strategy: dict = Field(default_factory=dict)
    methodology_summary: list[str] = Field(default_factory=list)
    body_markdown: str = ""
    schema_version: str | None = None

    # 2026-05-15 OR 2026-05-16 (either name acceptable)
    github: str | None = None        # 2026-05-15
    github_name: str | None = None   # 2026-05-16 — parser fills whichever exists

    # 2026-05-16 NEW fields (all optional, all preserved verbatim)
    task_objective: str | None = None
    task_inputs: list[dict] = Field(default_factory=list)
    task_outputs: list[dict] = Field(default_factory=list)
    executable: dict | None = None
    execution_profile: dict = Field(default_factory=dict)
    execution_environment: dict | None = None
    run_command: str | None = None
    run_cwd: str | None = None
    run_timeout_seconds: float | None = None
    reproducibility_tier: str | None = None
    expected_artifact_name: str | None = None
    linked_result_ids: list[str] = Field(default_factory=list)
    provenance_source: str | None = None
    source_package: str | None = None
    scenario_id: str | None = None
    evidence_snippets: list[dict] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)

    # 2026-05-15 only — gracefully empty when absent
    workflow_ports: dict = Field(default_factory=dict)
```

**Updated `card_parser._card_from_json`** — add fallback for `github_name`/`github` and copy new fields:

```python
def _card_from_json(*, task_id: str, structured: dict, body: str) -> ParsedCard:
    return ParsedCard(
        task_id=task_id,
        title=(structured.get("title")
               or structured.get("task_objective")
               or structured.get("research_question")
               or task_id),
        article_type=structured.get("article_type"),
        domain=structured.get("domain"),
        primary_domain=structured.get("primary_domain"),
        subdomains=structured.get("subdomains") or [],
        techniques=structured.get("techniques") or [],
        subtask_categories=structured.get("subtask_categories") or [],
        crossref_doi=structured.get("crossref_doi") or structured.get("doi"),
        github=structured.get("github"),
        github_name=structured.get("github_name") or structured.get("github"),
        tools_used=structured.get("tools") or [],
        skills_used=structured.get("skills") or [],
        data_in=structured.get("data_in") or [],
        data_out=structured.get("data_out") or [],
        expected_outputs=structured.get("expected_outputs") or [],
        landmark_outputs=structured.get("landmark_outputs") or [],
        parameters=structured.get("parameters") or [],
        domain_knowledge=structured.get("domain_knowledge") or [],
        evaluation_strategy=structured.get("evaluation_strategy") or {},
        methodology_summary=structured.get("methodology_summary") or [],
        workflow_ports=structured.get("workflow_ports") or {},
        body_markdown=body,
        schema_version=structured.get("schema_version"),
        # New 2026-05-16 fields
        task_objective=structured.get("task_objective"),
        task_inputs=structured.get("task_inputs") or [],
        task_outputs=structured.get("task_outputs") or [],
        executable=structured.get("executable"),
        execution_profile=structured.get("execution_profile") or {},
        execution_environment=structured.get("execution_environment"),
        run_command=structured.get("run_command"),
        run_cwd=structured.get("run_cwd"),
        run_timeout_seconds=structured.get("run_timeout_seconds"),
        reproducibility_tier=structured.get("reproducibility_tier"),
        expected_artifact_name=structured.get("expected_artifact_name"),
        linked_result_ids=structured.get("linked_result_ids") or [],
        provenance_source=structured.get("provenance_source"),
        source_package=structured.get("source_package"),
        scenario_id=structured.get("scenario_id"),
        evidence_snippets=structured.get("evidence_snippets") or [],
        keywords=structured.get("keywords") or [],
    )
```

**Updated `chunk_producer.card_to_paper`** — extend metadata dict with the new fields:

```python
def card_to_paper(card: ParsedCard, *, dag: WorkflowDag | None) -> Paper:
    md = {
        "content_kind": "workflow_card",
        "task_id": card.task_id,
        "task_card_title": card.title,
        "task_objective": card.task_objective,
        "article_type": card.article_type,
        "domain": card.domain,
        "primary_domain": card.primary_domain,
        "subdomains": list(card.subdomains),
        "techniques": list(card.techniques),
        "subtask_categories": list(card.subtask_categories),
        "tools_used": list(card.tools_used),
        "skills_used": list(card.skills_used),
        "paper_doi": card.crossref_doi,
        "paper_github": card.github_name or card.github,
        "inputs": list(card.data_in),
        "task_inputs": list(card.task_inputs),
        "task_outputs": list(card.task_outputs),
        "expected_outputs": list(card.expected_outputs),
        "expected_artifact_name": card.expected_artifact_name,
        "parameters": list(card.parameters),
        "evaluation_strategy": dict(card.evaluation_strategy),
        "executable": card.executable,
        "execution_profile": dict(card.execution_profile),
        "execution_environment": card.execution_environment,
        "run_command": card.run_command,
        "run_cwd": card.run_cwd,
        "run_timeout_seconds": card.run_timeout_seconds,
        "reproducibility_tier": card.reproducibility_tier,
        "linked_result_ids": list(card.linked_result_ids),
        "provenance_source": card.provenance_source,
        "source_package": card.source_package,
        "scenario_id": card.scenario_id,
        "schema_version": card.schema_version,
        "upstream_tasks": dag.upstream(card.task_id) if dag else [],
        "downstream_tasks": dag.downstream(card.task_id) if dag else [],
    }
    return Paper(
        id=f"asb_card:{card.task_id}",
        title=card.title,
        abstract=card.task_objective or "",
        full_text=card.body_markdown,
        source=PaperSource.SKILL_BUNDLE,
        doi=card.crossref_doi,
        metadata=md,
    )
```

**Updated `response.build_asb_response_metadata`** — surface `executable`, `execution_profile`, and the `task_inputs/outputs` ports on workflow_metadata entries:

```python
# in build_asb_response_metadata (Phase D Task 11), the workflow_metadata
# branch builds the dict for each task_id. Extend with:
workflow_map[task_id] = {
    # ...existing fields from the original sketch...
    "task_objective": md.get("task_objective"),
    "executable": md.get("executable"),                # dict; gold for run/no-run decision
    "execution_profile": md.get("execution_profile"),  # compute_tier etc.
    "task_inputs": md.get("task_inputs") or [],        # port records
    "task_outputs": md.get("task_outputs") or [],
    "expected_artifact_name": md.get("expected_artifact_name"),
    "run_timeout_seconds": md.get("run_timeout_seconds"),
    "reproducibility_tier": md.get("reproducibility_tier"),
}
```

- [ ] **No standalone commit for C3** — these patches are absorbed when Phase D Tasks 4, 6, 11 execute. The implementer subagent prompts MUST reference this task.

---

## Phase D — ASB Bundle Ingest (execute [`2026-05-15-asb-bundle-ingest.md`](2026-05-15-asb-bundle-ingest.md) tasks 1-12)

Execute each task in the original ASB plan in order. When a task overlaps with Phase C, **prefer the Phase C patch** (master plan wins).

For convenience, the 12 ASB tasks (with Phase C cross-references):

- [ ] **D-1: PaperSource.SKILL_BUNDLE enum** (Task 1 in ASB plan — verbatim)
- [ ] **D-2: Copy ASB fixture** (Task 2 in ASB plan — **superseded by Phase C1**; use the two fixtures from C1 instead of the single one in the original)
- [ ] **D-3: Skill parser** (Task 3 in ASB plan — verbatim; the 2026-05-16 skills directory has the same per-skill structure)
- [ ] **D-4: Workflow-card parser** (Task 4 in ASB plan — **superseded by Phase C3** for `ParsedCard` + `_card_from_json`)
- [ ] **D-5: workflow_dag.json reader** (Task 5 in ASB plan — **superseded by Phase C2** for the `dag.py` + dag tests)
- [ ] **D-6: Chunk producer** (Task 6 in ASB plan — **superseded by Phase C3** for `card_to_paper`)
- [ ] **D-7: skill_kb.json writer** (Task 7 in ASB plan — verbatim)
- [ ] **D-8: Top-level orchestrator** (Task 8 in ASB plan — verbatim)
- [ ] **D-9: MCP tool `ingest_asb_run`** (Task 9 in ASB plan — verbatim)
- [ ] **D-10: CLI command `ingest-asb-run`** (Task 10 in ASB plan — verbatim, Click-based per cli.py)
- [ ] **D-11: Response-layer `skill_metadata` + `workflow_metadata`** (Task 11 in ASB plan — **superseded by Phase C3** for `workflow_metadata` fields)
- [ ] **D-12: End-to-end integration test** (Task 12 in ASB plan — verbatim, but the live test uses the new `article_878_v4_subset` fixture)

Each task in Phase D follows the same TDD discipline as Phase A/B: write failing test → run to confirm fail → write minimal impl → run to confirm pass → commit. The full code/test sketches live in `2026-05-15-asb-bundle-ingest.md`.

---

## Phase E — Final validation + handoff

### Task E1: Full test suite green

**Files:** none directly — run the suite, fix any regressions.

- [ ] **Step 1: Run the full unit + integration slice**

```bash
PYTHONPATH=src pytest tests/unit tests/integration -q --tb=line
```
Expected: all green. The original 2026-05-15 baseline was 1316 passed / 1 skipped / 0 failed. After Phase A-D, expect ~1380+ passed (new tests from each task).

- [ ] **Step 2: Run the live-gated integration tests (manual)**

```bash
PYTHONPATH=src PERSPICACITE_LIVE_SEARCH=1 PERSPICACITE_E2E_ASB=1 pytest tests/integration/ -v
```
Expected: live tests pass with valid API keys. Failures should be flagged as live-env issues, not code regressions.

- [ ] **Step 3: Snapshot test counts in the handoff doc**

Save the final counts (passed / skipped / failed) for the Phase E2 handoff.

- [ ] **Step 4: No commit unless regressions surface.**

If regressions surface during E1, fix them inline as a new commit (e.g., `fix: regression from Phase X task Y`). Do not skip failing tests.

---

### Task E2: Generate the session-end handoff document

**Files:**
- Create: `docs/superpowers/handoffs/2026-05-15-master-execution-handoff.md`

- [ ] **Step 1: Write the handoff**

Follow the same template as [`docs/superpowers/handoffs/2026-05-15-session-end.md`](../handoffs/2026-05-15-session-end.md). Sections:

- **What landed this session** (per phase, list commits)
- **Backlog by priority** — what's left for the next session (e.g., Phase D follow-up: repo fetcher from parent skill-bundle plan; Scriptorium feedback follow-up; future ASB capsule v2)
- **Standing workflow** (copy from the prior handoff; this doesn't change)
- **Quick-start commands**
- **Pinned context** — branch state, worktree path, key memory entries
- **Files / paths a fresh session will want to grep first**

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/handoffs/2026-05-15-master-execution-handoff.md
git commit -m "docs(handoff): master-execution-plan session-end summary"
```

---

## Acceptance criteria (end-of-plan)

After all 26 tasks execute successfully:

- [ ] **Phase A** — All 5 Scriptorium critical fixes shipped with tests pinned. `mcp/server.py` no longer emits enum repr; `_json_ok` carries both envelope keys; `search_literature` default-databases regression diagnosed + fixed; `/api/chat` accepts `message` alias; README mentions MCP serving.
- [ ] **Phase B** — Title-normalize retry on zero results; per-backend search runs in parallel; tool docstrings carry latency hints; `/api/llm/proxy` is live with no RAG/KB coupling.
- [ ] **Phase C + D** — `perspicacite ingest-asb-run <run_dir>` works against both 2026-05-15 and 2026-05-16 ASB outputs. Response payloads carry `skill_metadata` + `workflow_metadata` (with `executable` dict for v2 cards). `skill_kb.json` round-trip stamps each ingested skill.
- [ ] **Phase E** — Full test suite green; handoff doc committed.

Total estimated wall time with subagent dispatch + two-stage review: **15-30 hours**. The plan is designed for autonomous rollover — the driver dispatches one implementer per task, reviews, and moves on without check-in.

## Out of scope (deferred to a future session)

- Repo fetching from `links.json[category=repo_github]` — covered by the parent [`2026-05-15-github-skill-bundle-ingest.md`](2026-05-15-github-skill-bundle-ingest.md) plan. Run that plan after the master plan if time permits.
- ASB capsules ingest (per-task RO-Crate containers) — explicit v2 item.
- ASB `scenarios/` dir ingest — new artifact stream, separate spec needed.
- Workflow DAG traversal as queryable graph nodes — graph-RAG extension.
- Hosting an ASB MCP server in this repo. Federation only.
- Migrating Scriptorium downstream client past the `ok` envelope alias — needs Scriptorium-side change.
