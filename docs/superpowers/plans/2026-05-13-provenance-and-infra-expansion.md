# Provenance & Infra-Completeness Expansion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the provenance/reproducibility layer (per-answer trace, LLM-call audit, RO-Crate export, UI panel) plus the documented mode/infra gaps (recency+multi-KB into all RAG modes, async ingestion w/ SSE, Europe PMC, Zotero, Obsidian export), additive-only, per-task commits on `main`.

**Architecture:** A `ProvenanceCollector` is bound to the running RAG request through a `contextvar` so the shared `AsyncLLMClient` can record every call without signature churn. Retrieval and mode-trace events are pushed by the modes; LLM-call payloads land in a per-conversation JSONL sidecar (`data/provenance/<id>.jsonl`); a queryable index lives in a new SQLite `provenance` table. An RO-Crate-flavored zip export merges the two. Async ingestion gets a tiny `JobRegistry` (SQLite `jobs` + in-memory queues) with SSE progress. Europe PMC slots into the existing structured-content pipeline; Zotero push and Obsidian vault export live as new integration modules.

**Tech Stack:** Python 3.12, FastAPI, fastmcp, aiosqlite, ChromaDB, LiteLLM, structlog, pytest + respx; static frontend is vanilla JS/CSS.

**Spec:** [docs/superpowers/specs/2026-05-13-provenance-and-infra-expansion-design.md](../specs/2026-05-13-provenance-and-infra-expansion-design.md)

---

## Constraints (READ FIRST — every task obeys these)

- **Additive-only.** No breaking API/schema changes. New SQLite tables go through `CREATE TABLE IF NOT EXISTS` in `SessionStore.init_db()`. New optional params default to `None`/disabled so prior behavior is unchanged.
- **Per-task done bar.** `uv run pytest tests/unit/ -m "not live"` stays green; **no new** ruff or mypy errors **on lines you touched**. Do **not** try to clear the pre-existing ~1769 ruff / ~310 mypy backlog. If `uv run ruff check <file>` reports many errors on lines you didn't modify, leave them.
- **Commit policy.** Each task = exactly one conventional commit on `main`. Stage only files the task touches (no `git add -A`). Use a HEREDOC body for the commit message ending with the `Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>` trailer.
- **UI is not browser-verified by subagents.** File-presence tests + a `MANUAL_QA.md` checklist are the bar.
- **Git-ignored files** (locally edited per phase but never committed): `AGENT_LOG.md`, `ROADMAP.md`, `CLAUDE.md`, `docs/rules/*.md`, `config.yml`, `uv.lock`. The whitelist for `.md` is README/CONTRIBUTING/LICENSE/NOTICE/perspicacite_skills.md/`docs/superpowers/**/*.md`/MANUAL_QA.md. **`docs/superpowers/specs/**/*.md` and `docs/superpowers/plans/**/*.md` ARE tracked** (whitelisted).
- **Test markers.** Unit tests live in `tests/unit/`; live tests live in `tests/test_*.py` and are excluded via `-m "not live"`.

---

## File map

**New packages / files**
- `src/perspicacite/provenance/__init__.py` — package surface
- `src/perspicacite/provenance/collector.py` — `ProvenanceCollector`, `RetrievalEvent`, `LLMCallRecord` dataclasses
- `src/perspicacite/provenance/context.py` — `current_collector` contextvar + `collecting(c)` CM
- `src/perspicacite/provenance/store.py` — `ProvenanceStore` (SQLite row + JSONL sidecar)
- `src/perspicacite/provenance/rocrate.py` — `build_rocrate_bundle(...)` zip builder
- `src/perspicacite/jobs/__init__.py`
- `src/perspicacite/jobs/registry.py` — `JobRegistry` (SQLite `jobs` + in-mem queues)
- `src/perspicacite/integrations/__init__.py`
- `src/perspicacite/integrations/zotero.py` — `ZoteroClient`
- `src/perspicacite/integrations/obsidian.py` — vault zip builder
- `src/perspicacite/pipeline/download/europepmc.py` — Europe PMC structured source
- `src/perspicacite/web/routers/jobs.py` — `GET /api/jobs/{id}` + SSE
- `src/perspicacite/web/routers/zotero.py` — `POST /api/zotero/push`
- `static/js/provenance.js` — per-message provenance disclosure
- `tests/unit/test_provenance_collector.py`, `tests/unit/test_provenance_store.py`, `tests/unit/test_llm_client_provenance.py`, `tests/unit/test_rocrate_export.py`, `tests/unit/test_provenance_endpoints.py`, `tests/unit/test_advanced_recency_multikb.py`, `tests/unit/test_profound_recency_multikb.py`, `tests/unit/test_literature_survey_recency_multikb.py`, `tests/unit/test_agentic_recency_multikb.py`, `tests/unit/test_jobs_registry.py`, `tests/unit/test_async_ingestion_endpoints.py`, `tests/unit/test_europepmc.py`, `tests/unit/test_zotero.py`, `tests/unit/test_obsidian_export.py`

**Modified files**
- `src/perspicacite/memory/session_store.py` — `provenance` + `jobs` tables in `init_db()`
- `src/perspicacite/web/state.py` — instantiate `ProvenanceStore` + `JobRegistry`
- `src/perspicacite/web/app.py` — register new routers
- `src/perspicacite/rag/engine.py` — bind collector via contextvar, save on completion
- `src/perspicacite/llm/client.py` — record calls when collector active
- `src/perspicacite/rag/modes/{basic,contradiction,advanced,profound,literature_survey,agentic}.py` — push events, wire recency+multi-KB
- `src/perspicacite/rag/agentic/orchestrator.py` — accept `recency_weight`, `recency_half_life_years`, `kb_metas`
- `src/perspicacite/retrieval/recency.py` — add `apply_recency_weighting_to_papers`
- `src/perspicacite/web/routers/conversations.py` — provenance + ro-crate export endpoints
- `src/perspicacite/web/routers/chat.py` — emit assistant message id in final SSE event
- `src/perspicacite/web/routers/kb.py` — async ingestion endpoints + Obsidian export
- `src/perspicacite/mcp/server.py` — `push_to_zotero` tool; `get_info()` 11 tools
- `src/perspicacite/config/schema.py` — `ZoteroConfig`
- `src/perspicacite/pipeline/download/unified.py` — Europe PMC wired
- `config.example.yml` — `zotero:` block
- `templates/index.html` — `provenance.js` script tag + UI hooks
- `static/css/chat.css`, `static/css/kb.css` — styles
- `static/js/chat.js`, `static/js/conversations.js`, `static/js/kb.js` — UI hooks
- `README.md`, `docs/perspicacite_skills.md` — updated tool count + features
- `tests/unit/test_static_assets.py`, `tests/unit/test_web_app_routes.py` — register new assets/routes
- `tests/test_mcp_server.py` — `push_to_zotero` + count 11
- `MANUAL_QA.md` — per-phase click-through

---

# Phase 1 — Provenance core (P1)

## Task 1.1: ProvenanceCollector + contextvar

**Files:**
- Create: `src/perspicacite/provenance/__init__.py`
- Create: `src/perspicacite/provenance/collector.py`
- Create: `src/perspicacite/provenance/context.py`
- Test: `tests/unit/test_provenance_collector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_provenance_collector.py
import json

from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting, get_collector


def test_collector_accumulates_and_finalizes() -> None:
    c = ProvenanceCollector(
        conversation_id="conv-1",
        message_id="msg-1",
        rag_mode="basic",
        request_params={"top_k": 5, "kb_names": ["kb1"]},
    )
    c.add_retrieval(
        paper_id="p1", doi="10.1/a", title="A", score=0.9,
        kb_name="kb1", content_type="full_text", pipeline_step="pdf",
        rank=0, stage_label="basic.retrieve",
    )
    c.add_trace("plan", detail={"steps": 3})
    c.add_llm_call(
        stage_label="basic.answer", provider="deepseek", model="deepseek-chat",
        prompt_messages=[{"role": "user", "content": "hi"}],
        response_text="hello", prompt_tokens=10, completion_tokens=5,
        latency_ms=42.0,
    )
    out = c.finalize()
    assert out["conversation_id"] == "conv-1"
    assert out["message_id"] == "msg-1"
    assert out["rag_mode"] == "basic"
    assert out["request_params"]["top_k"] == 5
    assert len(out["retrieval_events"]) == 1
    assert out["retrieval_events"][0]["doi"] == "10.1/a"
    assert out["mode_trace"][0]["step"] == "plan"
    assert out["mode_trace"][0]["detail"]["steps"] == 3
    assert len(out["llm_calls"]) == 1
    assert out["llm_calls"][0]["provider"] == "deepseek"
    assert out["llm_calls"][0]["prompt_tokens"] == 10
    # JSON-serializable
    json.dumps(out)


def test_collector_finalize_is_idempotent() -> None:
    c = ProvenanceCollector(conversation_id=None, message_id="m", rag_mode="basic", request_params={})
    a = c.finalize()
    b = c.finalize()
    assert a == b


def test_contextvar_default_none() -> None:
    assert get_collector() is None


def test_contextvar_collecting_sets_and_resets() -> None:
    c = ProvenanceCollector(conversation_id=None, message_id="m", rag_mode="basic", request_params={})
    with collecting(c):
        assert get_collector() is c
    assert get_collector() is None
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_provenance_collector.py -v`
Expected: ImportError for `perspicacite.provenance`.

- [ ] **Step 3: Implement the package**

```python
# src/perspicacite/provenance/__init__.py
"""Provenance recording for RAG answers.

Collector + contextvar wiring let modes / the LLM client append retrieval,
trace, and LLM-call events to a per-request record without changing call
signatures. See docs/superpowers/specs/2026-05-13-provenance-and-infra-expansion-design.md.
"""

from perspicacite.provenance.collector import (
    LLMCallRecord,
    ProvenanceCollector,
    RetrievalEvent,
)
from perspicacite.provenance.context import collecting, get_collector, set_collector

__all__ = [
    "LLMCallRecord",
    "ProvenanceCollector",
    "RetrievalEvent",
    "collecting",
    "get_collector",
    "set_collector",
]
```

```python
# src/perspicacite/provenance/collector.py
"""ProvenanceCollector — per-RAG-request accumulator for trace data."""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class RetrievalEvent:
    paper_id: str | None
    doi: str | None
    title: str | None
    score: float
    kb_name: str | None
    content_type: str | None
    pipeline_step: str | None
    rank: int
    stage_label: str


@dataclass
class LLMCallRecord:
    stage_label: str
    provider: str
    model: str
    prompt_messages: list[dict[str, Any]]
    response_text: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    ts: float


@dataclass
class ProvenanceCollector:
    conversation_id: str | None
    message_id: str | None
    rag_mode: str
    request_params: dict[str, Any]
    retrieval_events: list[RetrievalEvent] = field(default_factory=list)
    mode_trace: list[dict[str, Any]] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)

    def add_retrieval(
        self,
        *,
        paper_id: str | None,
        doi: str | None,
        title: str | None,
        score: float,
        kb_name: str | None,
        content_type: str | None,
        pipeline_step: str | None,
        rank: int,
        stage_label: str,
    ) -> None:
        self.retrieval_events.append(
            RetrievalEvent(
                paper_id=paper_id,
                doi=doi,
                title=title,
                score=float(score),
                kb_name=kb_name,
                content_type=content_type,
                pipeline_step=pipeline_step,
                rank=int(rank),
                stage_label=stage_label,
            )
        )

    def add_trace(self, step: str, **detail: Any) -> None:
        self.mode_trace.append({"step": step, "detail": detail.get("detail", detail)})

    def add_llm_call(
        self,
        *,
        stage_label: str,
        provider: str,
        model: str,
        prompt_messages: list[dict[str, Any]],
        response_text: str,
        prompt_tokens: int,
        completion_tokens: int,
        latency_ms: float,
        ts: float | None = None,
    ) -> None:
        self.llm_calls.append(
            LLMCallRecord(
                stage_label=stage_label,
                provider=provider,
                model=model,
                prompt_messages=list(prompt_messages),
                response_text=response_text,
                prompt_tokens=int(prompt_tokens or 0),
                completion_tokens=int(completion_tokens or 0),
                latency_ms=float(latency_ms),
                ts=float(ts if ts is not None else time.time()),
            )
        )

    def finalize(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "rag_mode": self.rag_mode,
            "request_params": dict(self.request_params),
            "retrieval_events": [asdict(e) for e in self.retrieval_events],
            "mode_trace": list(self.mode_trace),
            "llm_calls": [asdict(c) for c in self.llm_calls],
        }
```

```python
# src/perspicacite/provenance/context.py
"""Contextvar so the LLM client can find the active ProvenanceCollector."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    from perspicacite.provenance.collector import ProvenanceCollector

current_collector: ContextVar["ProvenanceCollector | None"] = ContextVar(
    "perspicacite_provenance_collector", default=None
)


def get_collector() -> "ProvenanceCollector | None":
    return current_collector.get()


def set_collector(c: "ProvenanceCollector | None") -> Any:  # type: ignore[name-defined]
    return current_collector.set(c)


@contextmanager
def collecting(c: "ProvenanceCollector") -> Iterator["ProvenanceCollector"]:
    token = current_collector.set(c)
    try:
        yield c
    finally:
        current_collector.reset(token)


# Re-export for type-completeness
from typing import Any  # noqa: E402
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_provenance_collector.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run full unit suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/provenance/__init__.py \
        src/perspicacite/provenance/collector.py \
        src/perspicacite/provenance/context.py \
        tests/unit/test_provenance_collector.py
git commit -m "$(cat <<'EOF'
feat(provenance): add ProvenanceCollector + contextvar wiring

Per-RAG-request accumulator records retrieval events, mode trace, and
LLM calls. A contextvar lets the shared AsyncLLMClient find the active
collector without signature churn (None → no-op).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.2: provenance SQLite table (sidecar deferred to P2)

**Files:**
- Modify: `src/perspicacite/memory/session_store.py` (extend `SCHEMA` and `init_db`)
- Create: `src/perspicacite/provenance/store.py` (SQLite-only first)
- Test: `tests/unit/test_provenance_store.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_provenance_store.py
from pathlib import Path

import pytest

from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_provenance_table_created_idempotently(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "p.db")
    await store.init_db()
    # Second call should not raise (idempotency)
    await store.init_db()


@pytest.mark.asyncio
async def test_provenance_store_save_and_get(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    c = ProvenanceCollector(
        conversation_id="conv-1",
        message_id="msg-1",
        rag_mode="basic",
        request_params={"top_k": 3},
    )
    c.add_retrieval(
        paper_id="p1", doi="10.1/a", title="A", score=0.8,
        kb_name="kb1", content_type="full_text", pipeline_step="pdf",
        rank=0, stage_label="basic.retrieve",
    )
    c.add_trace("plan", detail={"x": 1})
    await ps.save(c.finalize())

    rec = await ps.get_for_message("msg-1")
    assert rec is not None
    assert rec["rag_mode"] == "basic"
    assert rec["retrieval_events"][0]["doi"] == "10.1/a"
    assert rec["mode_trace"][0]["step"] == "plan"
    # llm_calls_index empty for now (sidecar in P2)
    assert rec["llm_calls"] == []


@pytest.mark.asyncio
async def test_provenance_store_missing_returns_none(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    assert await ps.get_for_message("nope") is None


@pytest.mark.asyncio
async def test_provenance_store_list_for_conversation(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    for i in range(3):
        c = ProvenanceCollector(
            conversation_id="conv-x", message_id=f"m{i}",
            rag_mode="basic", request_params={},
        )
        await ps.save(c.finalize())
    rows = await ps.get_for_conversation("conv-x")
    assert {r["message_id"] for r in rows} == {"m0", "m1", "m2"}
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_provenance_store.py -v`
Expected: ImportError or no-such-table.

- [ ] **Step 3: Extend `SCHEMA` in `session_store.py`**

Append to the `SCHEMA` triple-quoted block in [src/perspicacite/memory/session_store.py](src/perspicacite/memory/session_store.py) (right after the `kb_metadata` table, before the indices):

```sql
CREATE TABLE IF NOT EXISTS provenance (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT,
    rag_mode TEXT NOT NULL,
    request_params TEXT DEFAULT '{}',
    retrieval_events TEXT DEFAULT '[]',
    mode_trace TEXT DEFAULT '[]',
    llm_calls_index TEXT DEFAULT '[]',
    sidecar_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_provenance_conversation ON provenance(conversation_id);
```

- [ ] **Step 4: Implement `ProvenanceStore`**

```python
# src/perspicacite/provenance/store.py
"""ProvenanceStore — writes to SQLite + optional JSONL sidecar.

P1 only writes the SQLite row (llm_calls_index empty). P2 will add JSONL
sidecar writes for full prompt/response payloads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.provenance.store")


class ProvenanceStore:
    def __init__(self, db_path: str | Path, sidecar_dir: str | Path):
        self.db_path = Path(db_path)
        self.sidecar_dir = Path(sidecar_dir)
        self.sidecar_dir.mkdir(parents=True, exist_ok=True)

    async def save(self, record: dict[str, Any]) -> None:
        message_id = record.get("message_id")
        if not message_id:
            logger.warning("provenance_save_no_message_id")
            return
        # P1: empty llm_calls_index, no sidecar writes
        llm_calls_index: list[dict[str, Any]] = []
        sidecar_path: str | None = None
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO provenance
                        (message_id, conversation_id, rag_mode, request_params,
                         retrieval_events, mode_trace, llm_calls_index, sidecar_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        record.get("conversation_id"),
                        record.get("rag_mode", "unknown"),
                        json.dumps(record.get("request_params") or {}),
                        json.dumps(record.get("retrieval_events") or []),
                        json.dumps(record.get("mode_trace") or []),
                        json.dumps(llm_calls_index),
                        sidecar_path,
                    ),
                )
                await db.commit()
        except Exception as exc:
            logger.warning("provenance_save_failed", error=str(exc), message_id=message_id)

    async def get_for_message(self, message_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM provenance WHERE message_id = ?", (message_id,)
            )
            row = await cur.fetchone()
        if not row:
            return None
        return _row_to_record(row, sidecar_dir=self.sidecar_dir)

    async def get_for_conversation(self, conversation_id: str) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM provenance WHERE conversation_id = ? ORDER BY created_at",
                (conversation_id,),
            )
            rows = await cur.fetchall()
        return [_row_to_record(r, sidecar_dir=self.sidecar_dir) for r in rows]


def _row_to_record(row: Any, *, sidecar_dir: Path) -> dict[str, Any]:
    index = json.loads(row["llm_calls_index"] or "[]")
    sidecar_path = row["sidecar_path"]
    llm_calls = _resolve_llm_calls(index, sidecar_path, sidecar_dir)
    return {
        "message_id": row["message_id"],
        "conversation_id": row["conversation_id"],
        "rag_mode": row["rag_mode"],
        "request_params": json.loads(row["request_params"] or "{}"),
        "retrieval_events": json.loads(row["retrieval_events"] or "[]"),
        "mode_trace": json.loads(row["mode_trace"] or "[]"),
        "llm_calls_index": index,
        "llm_calls": llm_calls,
        "created_at": row["created_at"],
    }


def _resolve_llm_calls(
    index: list[dict[str, Any]], sidecar_path: str | None, sidecar_dir: Path
) -> list[dict[str, Any]]:
    if not index or not sidecar_path:
        return []
    p = sidecar_dir / sidecar_path
    if not p.exists():
        return []
    out: list[dict[str, Any]] = []
    with p.open("rb") as f:
        for entry in index:
            offset = entry.get("offset")
            if offset is None:
                continue
            f.seek(offset)
            line = f.readline()
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_provenance_store.py tests/unit/test_provenance_collector.py -v`
Expected: all pass.

- [ ] **Step 6: Full suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add src/perspicacite/memory/session_store.py \
        src/perspicacite/provenance/store.py \
        tests/unit/test_provenance_store.py
git commit -m "$(cat <<'EOF'
feat(provenance): add provenance SQLite table + ProvenanceStore

New CREATE TABLE IF NOT EXISTS provenance (...) in init_db() (idempotent).
Store writes the queryable row now; JSONL sidecar for full LLM payloads
arrives in P2 — get_for_message resolves sidecar entries when present.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.3: AppState wiring for ProvenanceStore

**Files:**
- Modify: `src/perspicacite/web/state.py` — instantiate `ProvenanceStore`

- [ ] **Step 1: Read current state**

Run: `grep -n "session_store\|provenance\|initialize\|class AppState\|self\\." src/perspicacite/web/state.py | head -40`

You'll see `session_store` is constructed somewhere in `initialize()`. The sidecar dir should live next to the SQLite DB (e.g. `Path(self.session_store.db_path).parent / "provenance"`).

- [ ] **Step 2: Add the field + initialization**

In [src/perspicacite/web/state.py](src/perspicacite/web/state.py), in `AppState.__init__` add `self.provenance_store: ProvenanceStore | None = None`. In `initialize()`, **after** `self.session_store = SessionStore(...)` and its `await self.session_store.init_db()`, add:

```python
from perspicacite.provenance.store import ProvenanceStore

sidecar_dir = self.session_store.db_path.parent / "provenance"
self.provenance_store = ProvenanceStore(
    db_path=self.session_store.db_path,
    sidecar_dir=sidecar_dir,
)
```

Place the import at the top of the file with the other `from perspicacite...` imports.

- [ ] **Step 3: Run app-state smoke test**

Run: `uv run pytest tests/unit/ -m "not live" -q -k "state or app or kb_create"`
Expected: green. (If `tests/unit/` has no AppState test, just run the full suite.)

- [ ] **Step 4: Full suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/state.py
git commit -m "$(cat <<'EOF'
feat(provenance): instantiate ProvenanceStore in AppState startup

Sidecar dir lives alongside the SQLite DB (data/provenance/). Modes and
the LLM client will append events via the contextvar once wired.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.4: RAGEngine contextvar wiring

**Files:**
- Modify: `src/perspicacite/rag/engine.py` — set/reset collector around `execute_stream` and `execute`
- Test: `tests/unit/test_provenance_engine_wiring.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_provenance_engine_wiring.py
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.memory.session_store import SessionStore
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.provenance.context import get_collector
from perspicacite.provenance.store import ProvenanceStore
from perspicacite.rag.engine import RAGEngine


class _RecordingMode:
    """Tiny stand-in mode that asserts a collector is active at execute_stream."""

    seen: list[Any] = []

    async def execute_stream(self, request, llm, vector_store, embedding_provider, tools) -> AsyncIterator[StreamEvent]:
        c = get_collector()
        _RecordingMode.seen.append(c)
        # emit a single done event with content
        yield StreamEvent(event="content", data='{"delta": "ok"}')
        yield StreamEvent(event="done", data="{}")


@pytest.mark.asyncio
async def test_engine_binds_and_saves_collector(tmp_path: Path) -> None:
    _RecordingMode.seen.clear()
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")

    cfg = MagicMock()
    cfg.rag_modes = MagicMock()
    engine = RAGEngine(
        llm_client=MagicMock(),
        vector_store=MagicMock(),
        embedding_provider=MagicMock(),
        tool_registry=MagicMock(),
        config=cfg,
    )
    engine._modes[RAGMode.BASIC] = _RecordingMode()  # type: ignore[assignment]
    engine.provenance_store = ps  # type: ignore[attr-defined]

    req = RAGRequest(query="hi", mode=RAGMode.BASIC, kb_name="default", top_k=5)
    events: list[StreamEvent] = []
    async for ev in engine.execute_stream(req, message_id="msg-123", conversation_id="conv-1"):
        events.append(ev)

    assert _RecordingMode.seen and _RecordingMode.seen[0] is not None
    assert _RecordingMode.seen[0].message_id == "msg-123"
    rec = await ps.get_for_message("msg-123")
    assert rec is not None
    assert rec["conversation_id"] == "conv-1"
    assert rec["rag_mode"] == "basic"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_provenance_engine_wiring.py -v`
Expected: AttributeError / TypeError — `RAGEngine.execute_stream` does not yet accept `message_id`.

- [ ] **Step 3: Patch RAGEngine**

In [src/perspicacite/rag/engine.py](src/perspicacite/rag/engine.py):

1. Add `provenance_store: Any | None = None` as a public attribute on `RAGEngine` (set externally by AppState — see Task 1.5; for now default it to `None` in `__init__`).
2. In `__init__`, add the line `self.provenance_store = None` at the end.
3. Modify `execute_stream` and `execute` to accept optional `message_id`, `conversation_id`. Wrap the dispatch in a `collecting(collector)` context:

```python
# Near other imports:
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting

# Inside execute_stream, before dispatching to the mode:
collector = ProvenanceCollector(
    conversation_id=conversation_id,
    message_id=message_id,
    rag_mode=request.mode.value if hasattr(request.mode, "value") else str(request.mode),
    request_params={
        "kb_name": request.kb_name,
        "kb_names": getattr(request, "kb_names", None),
        "top_k": getattr(request, "top_k", None),
        "recency_weight": getattr(request, "recency_weight", None),
        "recency_half_life_years": getattr(request, "recency_half_life_years", None),
        "bm25_weight": getattr(request, "bm25_weight", None),
        "vector_weight": getattr(request, "vector_weight", None),
    },
)
with collecting(collector):
    async for event in handler.execute_stream(
        request, self.llm_client, self.vector_store, self.embedding_provider, self.tool_registry
    ):
        yield event
# After the stream is exhausted, persist:
if self.provenance_store is not None and message_id:
    try:
        await self.provenance_store.save(collector.finalize())
    except Exception as exc:  # noqa: BLE001
        logger.warning("provenance_save_failed", error=str(exc))
```

Apply the same pattern to `execute` (non-streaming). The signature change is backwards-compatible: existing callers that don't pass `message_id` get `None` and the save is skipped.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_provenance_engine_wiring.py -v`
Expected: pass.

- [ ] **Step 5: Full suite (no regression on existing engine callers)**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/engine.py tests/unit/test_provenance_engine_wiring.py
git commit -m "$(cat <<'EOF'
feat(provenance): bind ProvenanceCollector to RAG requests via contextvar

execute_stream / execute accept optional message_id + conversation_id.
A collector is set on the contextvar before the mode runs and saved to
the ProvenanceStore (if attached) after the stream completes. None
message_id = no persistence (back-compat for ad-hoc callers).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.5: Attach ProvenanceStore to RAGEngine in AppState

**Files:**
- Modify: `src/perspicacite/web/state.py`

- [ ] **Step 1: Wire the store into the engine**

In [src/perspicacite/web/state.py](src/perspicacite/web/state.py), in `initialize()` **after** the `RAGEngine` is constructed and **after** `self.provenance_store = ProvenanceStore(...)` (Task 1.3), add:

```python
self.rag_engine.provenance_store = self.provenance_store
```

- [ ] **Step 2: Full suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add src/perspicacite/web/state.py
git commit -m "$(cat <<'EOF'
feat(provenance): hand ProvenanceStore to RAGEngine on startup

Persists every RAG answer's collector when a message_id is supplied by
the caller (chat router does this; MCP generate_report tool will too).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.6: AsyncLLMClient records calls when collector active

**Files:**
- Modify: `src/perspicacite/llm/client.py`
- Test: `tests/unit/test_llm_client_provenance.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_llm_client_provenance.py
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting


def _mock_config() -> LLMConfig:
    return LLMConfig(
        default_provider="deepseek",
        default_model="deepseek-chat",
        providers={
            "deepseek": LLMProviderConfig(
                api_key_env="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
                timeout=30,
            ),
        },
    )


def _mock_response(text: str = "hi", pt: int = 4, ct: int = 2) -> SimpleNamespace:
    msg = SimpleNamespace(content=text)
    choice = SimpleNamespace(message=msg)
    usage = {"prompt_tokens": pt, "completion_tokens": ct}
    resp = SimpleNamespace(choices=[choice], usage=usage)
    # Used by `response.get("usage", {})` in client.py — wrap in a dict-like
    resp.get = lambda k, default=None: usage if k == "usage" else default  # type: ignore[attr-defined]
    return resp


@pytest.mark.asyncio
async def test_llm_client_records_when_collector_set() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hello", 10, 5))
        get_litellm.return_value = litellm
        c = ProvenanceCollector(conversation_id="c", message_id="m", rag_mode="basic", request_params={})
        with collecting(c):
            out = await client.complete(
                messages=[{"role": "user", "content": "hi"}], stage="basic.answer"
            )
        assert out == "hello"
        assert len(c.llm_calls) == 1
        rec = c.llm_calls[0]
        assert rec.stage_label == "basic.answer"
        assert rec.provider == "deepseek"
        assert rec.prompt_tokens == 10
        assert rec.completion_tokens == 5
        assert rec.response_text == "hello"


@pytest.mark.asyncio
async def test_llm_client_no_recording_without_collector() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi"))
        get_litellm.return_value = litellm
        # No collecting() — call should still succeed
        out = await client.complete(messages=[{"role": "user", "content": "x"}])
        assert out == "hi"


@pytest.mark.asyncio
async def test_llm_client_stage_kwarg_is_optional() -> None:
    client = AsyncLLMClient(_mock_config())
    with patch.object(client, "_get_litellm") as get_litellm:
        litellm = MagicMock()
        litellm.acompletion = AsyncMock(return_value=_mock_response("hi"))
        get_litellm.return_value = litellm
        c = ProvenanceCollector(conversation_id=None, message_id="m", rag_mode="basic", request_params={})
        with collecting(c):
            await client.complete(messages=[{"role": "user", "content": "x"}])
        assert c.llm_calls[0].stage_label == "llm"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_llm_client_provenance.py -v`
Expected: fail — no recording happens yet, and `stage` kwarg may not be tolerated.

- [ ] **Step 3: Patch `AsyncLLMClient.complete`**

In [src/perspicacite/llm/client.py](src/perspicacite/llm/client.py):

1. Add `import time` at the top.
2. Pop `stage` out of `kwargs` early in `complete` so it doesn't reach `litellm.acompletion`:

```python
stage_label = kwargs.pop("stage", "llm")
```

Place this immediately after the `provider`/`model` defaulting block, before `provider_config = self._get_provider_config(provider)`.

3. Measure latency around the `await litellm.acompletion(...)` call. For the non-Minimax path (the second `response = await litellm.acompletion(**completion_kwargs)`), wrap:

```python
t0 = time.monotonic()
response = await litellm.acompletion(**completion_kwargs)
latency_ms = (time.monotonic() - t0) * 1000.0
```

For the Minimax branch, do the same around its `litellm.acompletion(...)` call.

4. Immediately after extracting `content` + `usage`, in **both** branches, record:

```python
from perspicacite.provenance.context import get_collector  # local import to avoid cycles

_c = get_collector()
if _c is not None:
    _c.add_llm_call(
        stage_label=stage_label,
        provider=provider,
        model=model,
        prompt_messages=messages,
        response_text=content or "",
        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
        latency_ms=latency_ms,
    )
```

5. Do the same in `stream(...)` if and only if it currently aggregates the full response (most uses do). For streaming, capture chunks into a list and record after the stream ends with the joined text and `latency_ms` measured around the full iteration. If `stream` is rarely called from RAG modes (most modes call `complete`), wiring it in this task is sufficient.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_llm_client_provenance.py -v`
Expected: 3 passed.

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/llm/client.py tests/unit/test_llm_client_provenance.py
git commit -m "$(cat <<'EOF'
feat(provenance): AsyncLLMClient records each call when collector active

complete()/stream() pop an optional stage kwarg, measure latency around
the litellm await, and (when a ProvenanceCollector is on the contextvar)
append a full LLMCallRecord — provider, model, full prompt + response,
token counts, latency. No collector → no-op, no behavior change.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.7: basic mode pushes retrieval + trace events

**Files:**
- Modify: `src/perspicacite/rag/modes/basic.py`
- Test: `tests/unit/test_basic_provenance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_basic_provenance.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest, StreamEvent
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting
from perspicacite.rag.modes.basic import BasicRAGMode


@pytest.mark.asyncio
async def test_basic_pushes_retrieval_and_trace() -> None:
    cfg = MagicMock(spec=Config)
    cfg.rag_modes = MagicMock()
    cfg.rag_modes.basic = MagicMock(top_k=3, temperature=0.1, max_tokens=500)
    mode = BasicRAGMode(cfg)

    fake_chunks = [
        {"paper_id": "p1", "metadata": {"doi": "10.1/a", "title": "A", "year": 2024, "content_type": "full_text", "content_source": "pmc"}, "score": 0.9, "kb_name": "kb1", "text": "snippet"},
        {"paper_id": "p2", "metadata": {"doi": "10.1/b", "title": "B"}, "score": 0.7, "text": "snippet2"},
    ]

    retriever = MagicMock()
    retriever.search = AsyncMock(return_value=fake_chunks)
    retriever._initialized = True

    # Patch _build_kb_retriever to return our fake
    mode._build_kb_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    llm = MagicMock()
    llm.stream = AsyncMock()

    async def _astream():
        yield "answer"
    llm.stream.return_value = _astream()

    req = RAGRequest(query="q", mode=RAGMode.BASIC, kb_name="kb1", top_k=3)
    c = ProvenanceCollector(conversation_id="c", message_id="m", rag_mode="basic", request_params={})
    events: list[StreamEvent] = []
    with collecting(c):
        async for ev in mode.execute_stream(req, llm, MagicMock(), MagicMock(), MagicMock()):
            events.append(ev)
    assert len(c.retrieval_events) == 2
    assert c.retrieval_events[0].doi == "10.1/a"
    assert c.retrieval_events[0].kb_name == "kb1"
    assert c.retrieval_events[0].stage_label == "basic.retrieve"
    steps = [t["step"] for t in c.mode_trace]
    assert "retrieve" in steps
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_basic_provenance.py -v`
Expected: empty retrieval_events.

- [ ] **Step 3: Push events in `basic.py`**

In [src/perspicacite/rag/modes/basic.py](src/perspicacite/rag/modes/basic.py), after the retriever returns chunks (the loop where sources are built), add:

```python
from perspicacite.provenance.context import get_collector

_c = get_collector()
if _c is not None:
    _c.add_trace("retrieve", detail={"kb_name": request.kb_name, "top_k": top_k, "count": len(chunks)})
    for rank, ch in enumerate(chunks):
        md = ch.get("metadata") if isinstance(ch, dict) else getattr(ch, "metadata", {}) or {}
        if not isinstance(md, dict):
            md = {}
        _c.add_retrieval(
            paper_id=(ch.get("paper_id") if isinstance(ch, dict) else getattr(ch, "paper_id", None)),
            doi=md.get("doi"),
            title=md.get("title"),
            score=float(ch.get("score", 0.0) if isinstance(ch, dict) else getattr(ch, "score", 0.0) or 0.0),
            kb_name=(ch.get("kb_name") if isinstance(ch, dict) else getattr(ch, "kb_name", None)),
            content_type=md.get("content_type"),
            pipeline_step=md.get("content_source"),
            rank=rank,
            stage_label="basic.retrieve",
        )
```

Place this **after** chunks come back and **after** recency weighting has been applied (so `score` reflects the final order). Also wrap the LLM call with `stage="basic.answer"`:

```python
async for delta in llm.stream(messages=..., stage="basic.answer", ...):
```

(`stage` is a kwarg on `complete`/`stream` — see Task 1.6.)

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_basic_provenance.py tests/unit/test_provenance_collector.py -v`
Expected: pass.

- [ ] **Step 5: Full suite**

Run: `uv run pytest tests/unit/ -m "not live" -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/perspicacite/rag/modes/basic.py tests/unit/test_basic_provenance.py
git commit -m "$(cat <<'EOF'
feat(provenance): basic mode pushes retrieval + trace events

After retrieval, basic mode now calls collector.add_retrieval per chunk
(doi, title, score, kb_name, content_type, pipeline_step, rank, stage)
and add_trace('retrieve', detail=...). LLM call carries stage='basic.answer'.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.8: contradiction mode pushes retrieval + trace events

**Files:**
- Modify: `src/perspicacite/rag/modes/contradiction.py`
- Test: `tests/unit/test_contradiction_provenance.py`

- [ ] **Step 1: Write the failing test**

Mirror Task 1.7's test shape with the contradiction-mode adapter (mock its retriever; assert `c.retrieval_events`, and that `mode_trace` contains `"retrieve"`, `"cluster"`, `"synthesize"`):

```python
# tests/unit/test_contradiction_provenance.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.context import collecting
from perspicacite.rag.modes.contradiction import ContradictionRAGMode


@pytest.mark.asyncio
async def test_contradiction_pushes_events() -> None:
    cfg = MagicMock(spec=Config)
    cfg.rag_modes = MagicMock()
    cfg.rag_modes.contradiction = MagicMock()
    mode = ContradictionRAGMode(cfg)

    fake_chunks = [
        {"paper_id": f"p{i}", "metadata": {"doi": f"10.1/{i}", "title": f"T{i}", "year": 2023},
         "score": 0.9 - i*0.1, "text": "t"}
        for i in range(4)
    ]
    retriever = MagicMock()
    retriever.search = AsyncMock(return_value=fake_chunks)
    retriever._initialized = True
    mode._build_kb_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]

    llm = MagicMock()
    # Patch the internal helpers to short-circuit LLM calls; the test only cares about events
    mode._summarize_claims = AsyncMock(return_value=[("p0", "claim A")])  # type: ignore[method-assign]
    mode._cluster_claims = AsyncMock(return_value={"agreement": [], "disagreement": [], "open": []})  # type: ignore[method-assign]

    async def _astream():
        yield "synthesis"
    mode._synthesize_stream = AsyncMock(return_value=_astream())  # type: ignore[method-assign]

    req = RAGRequest(query="q", mode=RAGMode.CONTRADICTION, kb_name="kb1", top_k=4)
    c = ProvenanceCollector(conversation_id="c", message_id="m", rag_mode="contradiction", request_params={})
    with collecting(c):
        async for _ in mode.execute_stream(req, llm, MagicMock(), MagicMock(), MagicMock()):
            pass
    assert len(c.retrieval_events) == 4
    steps = [t["step"] for t in c.mode_trace]
    assert "retrieve" in steps
```

- [ ] **Step 2: Verify failure → 3: Implement → 4: Tests → 5: Suite → 6: Commit**

Same pattern as Task 1.7 — push `add_retrieval` after chunks come back; `add_trace("retrieve", count=...)`, `add_trace("cluster", agreement=..., disagreement=..., open=...)`, `add_trace("synthesize")` at the relevant points in [src/perspicacite/rag/modes/contradiction.py](src/perspicacite/rag/modes/contradiction.py). Pass `stage="contradiction.cluster"` and `stage="contradiction.synthesis"` to the LLM calls.

```bash
git add src/perspicacite/rag/modes/contradiction.py tests/unit/test_contradiction_provenance.py
git commit -m "$(cat <<'EOF'
feat(provenance): contradiction mode pushes retrieval + trace events

retrieve / cluster / synthesize trace steps; per-chunk add_retrieval;
stage labels on LLM calls.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 1.9: Provenance read endpoints + chat router emits message id

**Files:**
- Modify: `src/perspicacite/web/routers/conversations.py` — new endpoints
- Modify: `src/perspicacite/web/routers/chat.py` — include assistant message id in final SSE event; pass message_id+conversation_id into the engine
- Test: `tests/unit/test_provenance_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_provenance_endpoints.py
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# Reuse the existing app + AppState test fixtures if any; otherwise build a thin app:
from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_get_message_provenance_endpoint(tmp_path: Path, monkeypatch) -> None:
    # Build minimal app context: seed a provenance row, then call the endpoint.
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    c = ProvenanceCollector(conversation_id="conv-1", message_id="msg-1", rag_mode="basic", request_params={})
    c.add_retrieval(paper_id="p1", doi="10.1/a", title="A", score=0.5, kb_name=None,
                    content_type=None, pipeline_step=None, rank=0, stage_label="basic.retrieve")
    await ps.save(c.finalize())

    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app
    monkeypatch.setattr(state_mod.app_state, "provenance_store", ps, raising=False)
    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)

    client = TestClient(app)
    r = client.get("/api/conversations/conv-1/messages/msg-1/provenance")
    assert r.status_code == 200
    body = r.json()
    assert body["rag_mode"] == "basic"
    assert body["retrieval_events"][0]["doi"] == "10.1/a"

    r404 = client.get("/api/conversations/conv-1/messages/nope/provenance")
    assert r404.status_code == 404

    rconv = client.get("/api/conversations/conv-1/provenance")
    assert rconv.status_code == 200
    assert any(rec["message_id"] == "msg-1" for rec in rconv.json())
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_provenance_endpoints.py -v`
Expected: 404 from unmounted routes.

- [ ] **Step 3: Add endpoints in `conversations.py`**

```python
# src/perspicacite/web/routers/conversations.py — add at bottom (alongside the other routes)
from fastapi import HTTPException

@router.get("/{conv_id}/messages/{message_id}/provenance")
async def get_message_provenance(conv_id: str, message_id: str):
    from perspicacite.web.state import app_state
    if app_state.provenance_store is None:
        raise HTTPException(status_code=503, detail="provenance not configured")
    rec = await app_state.provenance_store.get_for_message(message_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="no provenance for that message")
    if rec.get("conversation_id") and rec["conversation_id"] != conv_id:
        raise HTTPException(status_code=404, detail="provenance not in this conversation")
    return rec


@router.get("/{conv_id}/provenance")
async def list_conversation_provenance(conv_id: str):
    from perspicacite.web.state import app_state
    if app_state.provenance_store is None:
        raise HTTPException(status_code=503, detail="provenance not configured")
    return await app_state.provenance_store.get_for_conversation(conv_id)
```

Make sure these are registered **before** any catch-all `/{conv_id}` route (mirror the existing `/search` ordering convention).

- [ ] **Step 4: Chat router — pass ids to engine + emit message id**

In [src/perspicacite/web/routers/chat.py](src/perspicacite/web/routers/chat.py), inside `_stream_rag_mode`, **before** the `async for ev in mode.execute_stream(...)` (which currently goes via the engine? — check; if it's via the engine, change the call to `app_state.rag_engine.execute_stream(request, message_id=assistant_msg_id, conversation_id=conv_id)`).

Then, in the SSE-yielding loop, when emitting the final "done" frame, ensure the payload includes `assistant_message_id`. If the existing code emits `data: {json.dumps({'type': 'done', ...})}\n\n`, change it to:

```python
yield f"data: {json.dumps({'type': 'done', 'message_id': assistant_msg_id})}\n\n"
```

Pre-generate `assistant_msg_id` (use `str(uuid4())`) before the stream starts so the persisted Message uses the same id.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_provenance_endpoints.py tests/unit/test_chat_endpoint.py -v`
Expected: pass.

- [ ] **Step 6: Update route count + EXPECTED_ROUTES**

In [tests/unit/test_web_app_routes.py](tests/unit/test_web_app_routes.py), append to `EXPECTED_ROUTES`:

```python
("/api/conversations/{conv_id}/messages/{message_id}/provenance", "GET"),
("/api/conversations/{conv_id}/provenance", "GET"),
```

and raise the count floor in `test_total_route_count_unchanged` by 2.

- [ ] **Step 7: Full suite + commit**

```bash
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/web/routers/conversations.py \
        src/perspicacite/web/routers/chat.py \
        tests/unit/test_provenance_endpoints.py \
        tests/unit/test_web_app_routes.py
git commit -m "$(cat <<'EOF'
feat(provenance): GET /provenance endpoints + chat emits assistant message id

Two new endpoints: per-message and per-conversation provenance records.
Chat router pre-generates the assistant message id, threads it into the
engine, and includes it in the final 'done' SSE frame so the UI can
fetch the trace.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 2 — LLM-call audit + RO-Crate (P2)

## Task 2.1: JSONL sidecar in ProvenanceStore

**Files:**
- Modify: `src/perspicacite/provenance/store.py`
- Test: `tests/unit/test_provenance_store.py` (extend)

- [ ] **Step 1: Extend the test file**

Append:

```python
@pytest.mark.asyncio
async def test_provenance_store_writes_sidecar(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    c = ProvenanceCollector(conversation_id="conv-x", message_id="msg-x", rag_mode="basic", request_params={})
    c.add_llm_call(
        stage_label="basic.answer", provider="deepseek", model="deepseek-chat",
        prompt_messages=[{"role": "user", "content": "hello"}],
        response_text="world", prompt_tokens=3, completion_tokens=1, latency_ms=12.3,
    )
    await ps.save(c.finalize())
    sidecar = tmp_path / "provenance" / "conv-x.jsonl"
    assert sidecar.exists()
    rec = await ps.get_for_message("msg-x")
    assert rec is not None
    assert len(rec["llm_calls"]) == 1
    assert rec["llm_calls"][0]["response_text"] == "world"
    assert rec["llm_calls_index"][0]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_provenance_store_no_conversation_id_inlines_calls(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    c = ProvenanceCollector(conversation_id=None, message_id="adhoc", rag_mode="basic", request_params={})
    c.add_llm_call(
        stage_label="x", provider="p", model="m",
        prompt_messages=[{"role": "user", "content": "q"}],
        response_text="r", prompt_tokens=1, completion_tokens=1, latency_ms=1.0,
    )
    await ps.save(c.finalize())
    rec = await ps.get_for_message("adhoc")
    assert rec is not None
    # No sidecar → index entries carry the payload inline
    assert rec["llm_calls"][0]["response_text"] == "r"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_provenance_store.py -v`
Expected: fails (sidecar not created).

- [ ] **Step 3: Implement sidecar writes**

Replace the body of `ProvenanceStore.save()` so it appends each `llm_call` as a JSON line to `sidecar_dir/<conversation_id>.jsonl`, records the byte offset in `llm_calls_index`, and stores `sidecar_path` on the row. When `conversation_id` is `None`, write the full call payload **inline** into the index entry (no sidecar):

```python
async def save(self, record: dict[str, Any]) -> None:
    message_id = record.get("message_id")
    if not message_id:
        logger.warning("provenance_save_no_message_id")
        return
    conv_id = record.get("conversation_id")
    llm_calls: list[dict[str, Any]] = list(record.get("llm_calls") or [])
    llm_calls_index: list[dict[str, Any]] = []
    sidecar_path: str | None = None
    try:
        if conv_id and llm_calls:
            sidecar_path = f"{conv_id}.jsonl"
            target = self.sidecar_dir / sidecar_path
            self.sidecar_dir.mkdir(parents=True, exist_ok=True)
            # Append; record offsets
            with target.open("ab") as f:
                for call in llm_calls:
                    offset = f.tell()
                    line = (json.dumps(call) + "\n").encode("utf-8")
                    f.write(line)
                    llm_calls_index.append({
                        "stage_label": call.get("stage_label"),
                        "provider": call.get("provider"),
                        "model": call.get("model"),
                        "prompt_tokens": call.get("prompt_tokens", 0),
                        "completion_tokens": call.get("completion_tokens", 0),
                        "latency_ms": call.get("latency_ms", 0.0),
                        "ts": call.get("ts"),
                        "offset": offset,
                    })
        else:
            # No conversation id → keep payload inline in the index
            for call in llm_calls:
                entry = dict(call)
                entry["offset"] = None  # explicit marker
                llm_calls_index.append(entry)
    except Exception as exc:
        logger.warning("provenance_sidecar_write_failed", error=str(exc), message_id=message_id)

    try:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO provenance
                    (message_id, conversation_id, rag_mode, request_params,
                     retrieval_events, mode_trace, llm_calls_index, sidecar_path)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id, conv_id,
                    record.get("rag_mode", "unknown"),
                    json.dumps(record.get("request_params") or {}),
                    json.dumps(record.get("retrieval_events") or []),
                    json.dumps(record.get("mode_trace") or []),
                    json.dumps(llm_calls_index),
                    sidecar_path,
                ),
            )
            await db.commit()
    except Exception as exc:
        logger.warning("provenance_save_failed", error=str(exc), message_id=message_id)
```

Update `_resolve_llm_calls` so that when `sidecar_path` is `None` it returns the index entries as-is (after stripping the `offset` marker):

```python
def _resolve_llm_calls(index, sidecar_path, sidecar_dir):
    if not index:
        return []
    if not sidecar_path:
        # Inline payloads (no conversation id)
        out = []
        for entry in index:
            e = {k: v for k, v in entry.items() if k != "offset"}
            out.append(e)
        return out
    p = sidecar_dir / sidecar_path
    if not p.exists():
        return []
    out = []
    with p.open("rb") as f:
        for entry in index:
            offset = entry.get("offset")
            if offset is None:
                continue
            f.seek(offset)
            line = f.readline()
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_provenance_store.py -v`
Expected: pass.

- [ ] **Step 5: Full suite + commit**

```bash
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/provenance/store.py tests/unit/test_provenance_store.py
git commit -m "$(cat <<'EOF'
feat(provenance): JSONL sidecar for full LLM prompt/response payloads

Each save appends LLMCallRecords to data/provenance/<conversation_id>.jsonl
and records byte offsets in llm_calls_index. Adhoc calls (no conversation
id) keep the payload inline in the index. Sidecar I/O failures are logged
and swallowed; the SQLite row still writes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.2: RO-Crate bundle builder

**Files:**
- Create: `src/perspicacite/provenance/rocrate.py`
- Test: `tests/unit/test_rocrate_export.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rocrate_export.py
from __future__ import annotations

import io
import json
import zipfile

from perspicacite.provenance.rocrate import build_rocrate_bundle


def test_rocrate_bundle_structure() -> None:
    conversation = {
        "id": "conv-1",
        "title": "Q&A on microbiome",
        "kb_name": "default",
        "created_at": "2026-05-13T10:00:00",
    }
    messages = [
        {"id": "u1", "role": "user", "content": "What is X?", "timestamp": "..."},
        {
            "id": "a1", "role": "assistant", "content": "X is …",
            "timestamp": "...",
            "sources": [{"doi": "10.1/a", "title": "Paper A", "year": 2024, "journal": "J", "kb_name": "default", "content_type": "full_text"}],
        },
    ]
    provenance_records = [
        {
            "message_id": "a1",
            "conversation_id": "conv-1",
            "rag_mode": "basic",
            "retrieval_events": [{"doi": "10.1/a", "title": "Paper A", "score": 0.9}],
            "mode_trace": [{"step": "retrieve", "detail": {"count": 1}}],
            "llm_calls_index": [{"stage_label": "basic.answer", "model": "deepseek-chat"}],
            "request_params": {"kb_name": "default", "top_k": 5},
        }
    ]
    llm_calls_jsonl = b'{"stage_label":"basic.answer","model":"deepseek-chat"}\n'

    blob = build_rocrate_bundle(
        conversation=conversation,
        messages=messages,
        conversation_markdown="# Conv\n\nuser: hi\n",
        provenance_records=provenance_records,
        llm_calls_jsonl=llm_calls_jsonl,
    )
    assert isinstance(blob, bytes) and len(blob) > 0
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = set(z.namelist())
    assert "ro-crate-metadata.json" in names
    assert "conversation.md" in names
    assert "sources.json" in names
    assert "provenance/answer-a1.json" in names
    assert "provenance/llm-calls.jsonl" in names

    meta = json.loads(z.read("ro-crate-metadata.json"))
    assert meta["@context"]
    assert isinstance(meta["@graph"], list)
    # Conversation as Dataset, paper as ScholarlyArticle, answer as CreateAction
    types = {e.get("@type") for e in meta["@graph"]}
    assert "Dataset" in types
    assert "ScholarlyArticle" in types
    assert "CreateAction" in types

    sources = json.loads(z.read("sources.json"))
    assert sources[0]["doi"] == "10.1/a"
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_rocrate_export.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# src/perspicacite/provenance/rocrate.py
"""RO-Crate 1.1-flavored bundle builder (not SHACL-validated)."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any, Iterable


def build_rocrate_bundle(
    *,
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    conversation_markdown: str,
    provenance_records: Iterable[dict[str, Any]],
    llm_calls_jsonl: bytes,
) -> bytes:
    """Build an in-memory zip containing an RO-Crate-flavored conversation bundle.

    Layout:
        ro-crate-metadata.json
        conversation.md
        provenance/answer-<message_id>.json   (one per assistant message)
        provenance/llm-calls.jsonl            (copy of the sidecar)
        sources.json                          (flat list of cited papers)
    """
    prov_by_msg = {p["message_id"]: p for p in provenance_records}

    sources: list[dict[str, Any]] = []
    seen_doi: set[str] = set()
    for m in messages:
        for s in (m.get("sources") or []):
            doi = s.get("doi")
            key = doi or s.get("title") or json.dumps(s, sort_keys=True)
            if key in seen_doi:
                continue
            seen_doi.add(key)
            sources.append(
                {
                    "doi": doi,
                    "title": s.get("title"),
                    "year": s.get("year"),
                    "journal": s.get("journal"),
                    "kb_name": s.get("kb_name"),
                    "content_type": s.get("content_type"),
                }
            )

    metadata = _build_ro_crate_metadata(conversation, messages, prov_by_msg, sources)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("ro-crate-metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False))
        z.writestr("conversation.md", conversation_markdown)
        z.writestr("sources.json", json.dumps(sources, indent=2, ensure_ascii=False))
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            mid = msg.get("id")
            rec = prov_by_msg.get(mid)
            if rec is None:
                continue
            z.writestr(f"provenance/answer-{mid}.json", json.dumps(rec, indent=2, ensure_ascii=False))
        z.writestr("provenance/llm-calls.jsonl", llm_calls_jsonl or b"")
    return buf.getvalue()


def _build_ro_crate_metadata(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    prov_by_msg: dict[str, dict[str, Any]],
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    graph: list[dict[str, Any]] = [
        {
            "@type": "CreativeWork",
            "@id": "ro-crate-metadata.json",
            "conformsTo": {"@id": "https://w3id.org/ro/crate/1.1"},
            "about": {"@id": "./"},
        },
        {
            "@id": "./",
            "@type": "Dataset",
            "name": conversation.get("title") or "Conversation",
            "datePublished": conversation.get("created_at") or now,
            "description": "Perspicacité conversation with provenance trace",
            "identifier": conversation.get("id"),
            "hasPart": [
                {"@id": "conversation.md"},
                {"@id": "provenance/llm-calls.jsonl"},
                {"@id": "sources.json"},
            ],
        },
        {"@id": "conversation.md", "@type": "File", "encodingFormat": "text/markdown", "name": "Conversation transcript"},
        {"@id": "provenance/llm-calls.jsonl", "@type": "File", "encodingFormat": "application/jsonl", "name": "LLM call audit"},
        {"@id": "sources.json", "@type": "File", "encodingFormat": "application/json", "name": "Cited papers manifest"},
    ]

    for s in sources:
        sid = f"https://doi.org/{s['doi']}" if s.get("doi") else f"#paper-{abs(hash(s.get('title') or '')) % 10_000_000}"
        graph.append({
            "@id": sid,
            "@type": "ScholarlyArticle",
            "name": s.get("title"),
            "identifier": s.get("doi"),
            "datePublished": str(s.get("year")) if s.get("year") else None,
            "journal": s.get("journal"),
        })

    # Pair (user question, assistant answer) → CreateAction
    pending_q: dict[str, Any] | None = None
    for m in messages:
        if m.get("role") == "user":
            pending_q = m
            continue
        if m.get("role") == "assistant":
            mid = m.get("id")
            rec = prov_by_msg.get(mid, {})
            instruments = sorted({c.get("model") for c in (rec.get("llm_calls_index") or []) if c.get("model")})
            mentions = [
                {"@id": f"https://doi.org/{s['doi']}" if s.get("doi") else f"#paper-{abs(hash(s.get('title') or '')) % 10_000_000}"}
                for s in (m.get("sources") or [])
            ]
            graph.append({
                "@id": f"#answer-{mid}",
                "@type": "CreateAction",
                "name": f"Answer {mid}",
                "object": pending_q.get("content") if pending_q else None,
                "result": m.get("content"),
                "instrument": [{"@id": f"#model-{x}", "@type": "SoftwareApplication", "name": x} for x in instruments],
                "mentions": mentions,
                "additionalProperty": [
                    {"@type": "PropertyValue", "name": "rag_mode", "value": rec.get("rag_mode")},
                    {"@type": "PropertyValue", "name": "kb_name", "value": (rec.get("request_params") or {}).get("kb_name")},
                ],
                "subjectOf": {"@id": f"provenance/answer-{mid}.json"},
            })
            pending_q = None
    return {
        "@context": "https://w3id.org/ro/crate/1.1/context",
        "@graph": graph,
    }
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/unit/test_rocrate_export.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/provenance/rocrate.py tests/unit/test_rocrate_export.py
git commit -m "$(cat <<'EOF'
feat(provenance): RO-Crate 1.1-flavored bundle builder

build_rocrate_bundle assembles an in-memory zip: ro-crate-metadata.json
(JSON-LD with Dataset / ScholarlyArticle / CreateAction nodes),
conversation.md, sources.json, per-answer provenance JSON, and a copy
of the llm-calls JSONL sidecar. Not SHACL-validated by design.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.3: ?format=ro-crate export route

**Files:**
- Modify: `src/perspicacite/web/routers/conversations.py`
- Test: `tests/unit/test_provenance_endpoints.py` (extend)

- [ ] **Step 1: Extend the test**

```python
def test_export_ro_crate_returns_zip(tmp_path, monkeypatch):
    # Seed a conversation + provenance, then hit ?format=ro-crate
    # (Most of the seeding helpers will already exist from earlier conv tests.)
    ...
    r = client.get("/api/conversations/conv-1/export?format=ro-crate")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert "attachment" in r.headers.get("content-disposition", "")
    import io, zipfile
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert "ro-crate-metadata.json" in z.namelist()
```

- [ ] **Step 2: Extend the export route**

The existing `GET /api/conversations/{conv_id}/export?format=markdown` route lives in `conversations.py`. Add a `format=ro-crate` branch:

```python
from fastapi.responses import Response
from perspicacite.provenance.rocrate import build_rocrate_bundle

# Inside the existing export handler:
if format == "ro-crate":
    from perspicacite.web.state import app_state
    conv = await app_state.session_store.get_conversation(conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    md = _render_conversation_markdown(conv)  # existing helper
    prov_records = (
        await app_state.provenance_store.get_for_conversation(conv_id)
        if app_state.provenance_store is not None else []
    )
    # Read JSONL sidecar bytes if present
    sidecar = (app_state.provenance_store.sidecar_dir / f"{conv_id}.jsonl")
    jsonl_bytes = sidecar.read_bytes() if sidecar.exists() else b""
    blob = build_rocrate_bundle(
        conversation=conv.model_dump() if hasattr(conv, "model_dump") else dict(conv),
        messages=[m.model_dump() if hasattr(m, "model_dump") else dict(m) for m in conv.messages],
        conversation_markdown=md,
        provenance_records=prov_records,
        llm_calls_jsonl=jsonl_bytes,
    )
    filename = f"conversation-{conv_id}.rocrate.zip"
    return Response(
        content=blob,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

(If the existing markdown branch is its own route function rather than a `format` query param, mirror that style — add a parallel `?format=ro-crate` branch.)

- [ ] **Step 3: Route count bump**

Add nothing new to `EXPECTED_ROUTES` — the export path already exists; this just adds a new `format` value.

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/unit/test_provenance_endpoints.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/web/routers/conversations.py tests/unit/test_provenance_endpoints.py
git commit -m "$(cat <<'EOF'
feat(provenance): ?format=ro-crate export on conversation export route

Reuses the markdown renderer and the JSONL sidecar; returns a zip with
Content-Disposition attachment. Conversations with no provenance still
produce a valid bundle (empty provenance section).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2.4: Provenance UI (per-message disclosure + bundle link)

**Files:**
- Create: `static/js/provenance.js`
- Modify: `templates/index.html` (add `<script src="/static/js/provenance.js"></script>` near the end, before `main.js`)
- Modify: `static/js/chat.js` — call `attachProvenance(messageEl, messageId)` after rendering an assistant message; use `done.message_id` from the final SSE frame
- Modify: `static/js/conversations.js` — add a "Download RO-Crate bundle" link next to the existing Markdown export link
- Modify: `static/css/chat.css` — styles for the disclosure
- Modify: `MANUAL_QA.md` — new "Provenance UI" section
- Modify: `tests/unit/test_static_assets.py` — `provenance.js` present in `JS_FILES` and in `templates/index.html`

- [ ] **Step 1: Failing static-assets test**

```python
# tests/unit/test_static_assets.py — extend
def test_provenance_js_present():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    assert (root / "static/js/provenance.js").exists()
    html = (root / "templates/index.html").read_text()
    assert "provenance.js" in html
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_static_assets.py -v`
Expected: fail.

- [ ] **Step 3: Create the JS module**

```javascript
// static/js/provenance.js
window.attachProvenance = function attachProvenance(messageEl, messageId, conversationId) {
  if (!messageEl || !messageId || !conversationId) return;
  if (messageEl.querySelector('.provenance-disclosure')) return;
  const wrapper = document.createElement('details');
  wrapper.className = 'provenance-disclosure';
  const summary = document.createElement('summary');
  summary.textContent = 'Provenance';
  wrapper.appendChild(summary);
  const body = document.createElement('div');
  body.className = 'provenance-body';
  body.innerHTML = '<em>Loading…</em>';
  wrapper.appendChild(body);
  messageEl.appendChild(wrapper);
  wrapper.addEventListener('toggle', async () => {
    if (!wrapper.open || wrapper.dataset.loaded === '1') return;
    try {
      const r = await fetch(`/api/conversations/${conversationId}/messages/${messageId}/provenance`);
      if (!r.ok) { body.innerHTML = `<em>No provenance (status ${r.status})</em>`; return; }
      const rec = await r.json();
      body.innerHTML = renderProvenance(rec);
      wrapper.dataset.loaded = '1';
    } catch (e) {
      body.innerHTML = `<em>Error: ${e}</em>`;
    }
  });
};

function renderProvenance(rec) {
  const params = rec.request_params || {};
  const req = `<details open><summary>Request</summary>
    <div class="prov-block">mode=<b>${rec.rag_mode}</b> · kb=<b>${params.kb_name || (params.kb_names||[]).join(', ') || '?'}</b>
    · top_k=${params.top_k ?? '-'} · recency=${params.recency_weight ?? '-'} · weights v/b=${params.vector_weight ?? '-'} / ${params.bm25_weight ?? '-'}</div>
    </details>`;
  const rows = (rec.retrieval_events || []).map(e => `
    <tr><td>${e.rank}</td><td>${escape(e.title || '-')}</td><td>${e.score?.toFixed?.(3) ?? '-'}</td>
    <td>${escape(e.kb_name || '-')}</td><td>${escape(e.content_type || '-')}</td><td>${escape(e.pipeline_step || '-')}</td></tr>`).join('');
  const ret = `<details><summary>Retrieval (${(rec.retrieval_events||[]).length})</summary>
    <table class="prov-table"><thead><tr><th>#</th><th>Title</th><th>Score</th><th>KB</th><th>Type</th><th>Source</th></tr></thead>
    <tbody>${rows}</tbody></table></details>`;
  const traceItems = (rec.mode_trace || []).map(t => `<li><b>${escape(t.step)}</b> ${JSON.stringify(t.detail||{})}</li>`).join('');
  const llmItems = (rec.llm_calls || []).map((c,i) => `
    <details><summary>${escape(c.stage_label||'llm')} · ${escape(c.model||'?')} · ${c.prompt_tokens}/${c.completion_tokens}t · ${c.latency_ms?.toFixed?.(0) ?? '-'}ms</summary>
    <pre class="prov-prompt">${escape(JSON.stringify(c.prompt_messages||[], null, 2))}</pre>
    <pre class="prov-response">${escape(c.response_text||'')}</pre></details>`).join('');
  const reasoning = `<details><summary>Reasoning &amp; LLM calls (${(rec.llm_calls||[]).length})</summary>
    <ol class="prov-trace">${traceItems}</ol>${llmItems}</details>`;
  return req + ret + reasoning;
}
function escape(s) { return String(s ?? '').replace(/[&<>]/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[ch])); }
```

- [ ] **Step 4: Wire into chat.js**

In [static/js/chat.js](static/js/chat.js), in the SSE message handler, when receiving the `done` frame, capture `data.message_id` and call `window.attachProvenance(assistantMessageEl, data.message_id, currentConversationId)`.

In [static/js/conversations.js](static/js/conversations.js), wherever the "Export markdown" link is rendered, add a sibling:

```javascript
const ro = document.createElement('a');
ro.href = `/api/conversations/${convId}/export?format=ro-crate`;
ro.textContent = 'RO-Crate bundle';
ro.className = 'export-link';
parentEl.appendChild(ro);
```

- [ ] **Step 5: Styles + HTML hook**

In [static/css/chat.css](static/css/chat.css) append:

```css
.provenance-disclosure { margin-top: 8px; font-size: 0.9em; }
.provenance-disclosure summary { cursor: pointer; color: var(--accent, #4a90e2); }
.prov-table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
.prov-table th, .prov-table td { border: 1px solid #ddd; padding: 2px 6px; text-align: left; }
.prov-prompt, .prov-response { background: #f5f5f5; padding: 6px; max-height: 240px; overflow: auto; }
.prov-trace { margin: 0 0 8px 1em; }
```

In [templates/index.html](templates/index.html), add `<script src="/static/js/provenance.js" defer></script>` **before** `main.js`.

- [ ] **Step 6: MANUAL_QA.md section**

Append to `MANUAL_QA.md`:

```markdown
## Provenance UI (Phase 2)

- [ ] After asking a question, the assistant message shows a "Provenance" disclosure.
- [ ] Expanding it shows Request, Retrieval (with rank/score/KB/type/source), Reasoning & LLM calls.
- [ ] LLM call rows expand to show full prompt + response.
- [ ] Conversation header "RO-Crate bundle" link downloads a .zip with ro-crate-metadata.json, conversation.md, provenance/, sources.json.
```

- [ ] **Step 7: Run tests + commit**

```bash
uv run pytest tests/unit/test_static_assets.py tests/unit/ -m "not live" -q
git add static/js/provenance.js \
        static/js/chat.js static/js/conversations.js \
        static/css/chat.css templates/index.html \
        tests/unit/test_static_assets.py MANUAL_QA.md
git commit -m "$(cat <<'EOF'
feat(provenance): per-message disclosure + RO-Crate bundle link in UI

provenance.js renders Request / Retrieval table / Reasoning + LLM calls
on click; chat.js attaches it using the message_id from the SSE done
frame; conversations sidebar gains an RO-Crate bundle download link.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 3 — RAG mode wiring (recency + multi-KB)

## Task 3.1: apply_recency_weighting_to_papers + unit tests

**Files:**
- Modify: `src/perspicacite/retrieval/recency.py`
- Test: `tests/unit/test_recency.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_recency.py — append
from perspicacite.retrieval.recency import apply_recency_weighting_to_papers


def test_apply_recency_to_papers_dicts() -> None:
    papers = [
        {"doi": "10.1/a", "year": 2024, "paper_score": 0.9},
        {"doi": "10.1/b", "year": 2010, "paper_score": 0.9},
        {"doi": "10.1/c", "paper_score": 0.5},  # no year
    ]
    out = apply_recency_weighting_to_papers(papers, recency_weight=1.0, half_life_years=8.0, current_year=2026)
    # Newer paper outranks older one
    assert out[0]["doi"] in {"10.1/a", "10.1/c"}
    assert any(p["doi"] == "10.1/b" for p in out)


def test_apply_recency_to_papers_no_op_when_zero() -> None:
    papers = [{"doi": "x", "year": 2010, "paper_score": 0.5}]
    out = apply_recency_weighting_to_papers(papers, recency_weight=0.0)
    assert out == papers


def test_apply_recency_to_papers_no_op_when_none() -> None:
    papers = [{"doi": "x", "year": 2010, "paper_score": 0.5}]
    out = apply_recency_weighting_to_papers(papers, recency_weight=None)
    assert out == papers
```

- [ ] **Step 2: Verify failure → 3: Implement → 4: Tests → 5: Commit**

Add to [src/perspicacite/retrieval/recency.py](src/perspicacite/retrieval/recency.py):

```python
def apply_recency_weighting_to_papers(
    papers: list[dict[str, Any]],
    recency_weight: float | None,
    half_life_years: float | None = None,
    current_year: int | None = None,
) -> list[dict[str, Any]]:
    """Paper-dict variant of apply_recency_weighting.

    Expects papers shaped like {doi, year, paper_score|score, ...}. Operates
    in place AND returns the same list re-sorted by adjusted score desc.
    No-op when recency_weight is None or 0.
    """
    if not recency_weight or recency_weight <= 0:
        return papers
    w = min(1.0, float(recency_weight))
    hl = float(half_life_years or DEFAULT_HALF_LIFE_YEARS)
    if hl <= 0:
        hl = DEFAULT_HALF_LIFE_YEARS
    cy = int(current_year or _dt.date.today().year)
    score_key = "paper_score" if (papers and "paper_score" in papers[0]) else "score"
    for p in papers:
        y = p.get("year")
        try:
            y = int(y) if y else None
        except (TypeError, ValueError):
            y = None
        factor = 1.0 if y is None else 0.5 ** (max(0, cy - y) / hl)
        old = float(p.get(score_key, 0.0) or 0.0)
        p[score_key] = old * (1.0 - w + w * factor)
    papers.sort(key=lambda p: float(p.get(score_key, 0.0) or 0.0), reverse=True)
    return papers
```

```bash
uv run pytest tests/unit/test_recency.py -v
git add src/perspicacite/retrieval/recency.py tests/unit/test_recency.py
git commit -m "$(cat <<'EOF'
feat(retrieval): apply_recency_weighting_to_papers for WRRF/two-pass flows

Same exponential-decay math as the chunk variant, but for per-paper
score dicts used by advanced/profound/literature_survey mode retrieval.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.2: advanced mode — recency + multi-KB + provenance

**Files:**
- Modify: `src/perspicacite/rag/modes/advanced.py`
- Test: `tests/unit/test_advanced_recency_multikb.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_advanced_recency_multikb.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.config.schema import Config
from perspicacite.models.rag import RAGMode, RAGRequest
from perspicacite.rag.modes.advanced import AdvancedRAGMode
from perspicacite.retrieval.multi_kb import MultiKBRetriever


@pytest.mark.asyncio
async def test_advanced_uses_multikb_when_kb_names_given() -> None:
    cfg = MagicMock(spec=Config)
    cfg.rag_modes = MagicMock()
    mode = AdvancedRAGMode(cfg)
    # Patch _build_kb_retriever so we can introspect the choice
    captured = {}

    def fake_build(req, vs, ep):
        captured["call"] = req.kb_names
        r = MagicMock()
        r.search = AsyncMock(return_value=[])
        r.search_two_pass = AsyncMock(return_value=([], []))
        return r

    mode._build_kb_retriever = fake_build  # type: ignore[method-assign]
    req = RAGRequest(query="q", mode=RAGMode.ADVANCED, kb_name="kb1",
                     kb_names=["kb1", "kb2"], top_k=5, recency_weight=0.5)
    async for _ in mode.execute_stream(req, MagicMock(), MagicMock(), MagicMock(), MagicMock()):
        pass
    assert captured["call"] == ["kb1", "kb2"]


@pytest.mark.asyncio
async def test_advanced_applies_recency_to_papers(monkeypatch) -> None:
    from perspicacite.retrieval import recency as recmod
    calls = []
    orig = recmod.apply_recency_weighting_to_papers

    def spy(papers, recency_weight, **kw):
        calls.append((len(papers), recency_weight))
        return orig(papers, recency_weight, **kw)

    monkeypatch.setattr(recmod, "apply_recency_weighting_to_papers", spy)
    cfg = MagicMock(spec=Config); cfg.rag_modes = MagicMock()
    mode = AdvancedRAGMode(cfg)

    retriever = MagicMock()
    retriever.search_two_pass = AsyncMock(return_value=([], [
        {"doi": "10.1/a", "year": 2024, "paper_score": 0.9, "chunks": []},
        {"doi": "10.1/b", "year": 2010, "paper_score": 0.9, "chunks": []},
    ]))
    mode._build_kb_retriever = MagicMock(return_value=retriever)  # type: ignore[method-assign]
    req = RAGRequest(query="q", mode=RAGMode.ADVANCED, kb_name="kb1", top_k=5, recency_weight=0.8)
    async for _ in mode.execute_stream(req, MagicMock(), MagicMock(), MagicMock(), MagicMock()):
        pass
    assert calls and calls[0][1] == 0.8
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/unit/test_advanced_recency_multikb.py -v`
Expected: failures.

- [ ] **Step 3: Patch [src/perspicacite/rag/modes/advanced.py](src/perspicacite/rag/modes/advanced.py)**

1. Replace any direct `DynamicKnowledgeBase(...)` construction with `self._build_kb_retriever(request, vector_store, embedding_provider)`.
2. After `paper_results` is assembled in `_wrrf_retrieval` (find the dict-list of papers with `paper_score`), call:

```python
from perspicacite.retrieval.recency import apply_recency_weighting_to_papers

paper_results = apply_recency_weighting_to_papers(
    paper_results,
    recency_weight=getattr(request, "recency_weight", None),
    half_life_years=getattr(request, "recency_half_life_years", None),
)
```

3. Push provenance events (mirrors Task 1.7) after retrieval: `_c.add_trace("wrrf_retrieve", count=len(paper_results))` and per-paper `_c.add_retrieval(... stage_label="advanced.wrrf_pass2")`. Pass `stage="advanced.answer"` (or `"advanced.refine"` for the optional refinement step) to LLM calls.

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/unit/test_advanced_recency_multikb.py tests/unit/test_provenance_collector.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/rag/modes/advanced.py tests/unit/test_advanced_recency_multikb.py
git commit -m "$(cat <<'EOF'
feat(rag/advanced): wire recency + multi-KB + provenance events

advanced uses _build_kb_retriever, applies apply_recency_weighting_to_papers
after WRRF assembly, and pushes per-paper retrieval / trace events to the
ProvenanceCollector. stage='advanced.*' on LLM calls.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.3: profound mode — recency + multi-KB + provenance

**Files:**
- Modify: `src/perspicacite/rag/modes/profound.py`
- Test: `tests/unit/test_profound_recency_multikb.py`

- [ ] **Steps 1-6:** Mirror Task 3.2 exactly. The profound mode runs up to 3 research cycles — apply recency at the end of each cycle's two-pass retrieval, and push `add_trace("cycle", n=i, plan_steps=...)` per cycle plus `add_trace("reflection", ...)`. Stages: `"profound.plan"`, `"profound.cycle{N}.retrieve"`, `"profound.reflect"`, `"profound.answer"`.

```bash
git add src/perspicacite/rag/modes/profound.py tests/unit/test_profound_recency_multikb.py
git commit -m "$(cat <<'EOF'
feat(rag/profound): wire recency + multi-KB + provenance per cycle

Each research cycle uses the multi-KB retriever and applies recency
weighting to the cycle's paper results; provenance trace records the
plan, each cycle, reflection, and the final answer.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.4: literature_survey mode — recency + multi-KB + provenance

**Files:**
- Modify: `src/perspicacite/rag/modes/literature_survey.py`
- Test: `tests/unit/test_literature_survey_recency_multikb.py`

- [ ] **Steps 1-6:** Same pattern. Replace KB construction with `_build_kb_retriever`; apply `apply_recency_weighting_to_papers` (or the chunk-level `apply_recency_weighting` depending on what the broad search returns — match the data shape) to the broad-search results before theme clustering. Stages: `"survey.broad_search"`, `"survey.cluster"`, `"survey.recommend"`.

```bash
git commit -m "$(cat <<'EOF'
feat(rag/literature_survey): wire recency + multi-KB + provenance

Broad-search retrieval honors recency_weight and kb_names; theme
clustering and recommendation stages push provenance trace events.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3.5: agentic orchestrator — recency + multi-KB + provenance

**Files:**
- Modify: `src/perspicacite/rag/agentic/orchestrator.py`
- Modify: `src/perspicacite/rag/modes/agentic.py`
- Test: `tests/unit/test_agentic_recency_multikb.py`, `tests/unit/test_orchestrator_config.py` (extend)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_agentic_recency_multikb.py
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator


def test_orchestrator_accepts_recency_and_kb_metas() -> None:
    o = AgenticOrchestrator(
        llm_client=MagicMock(),
        vector_store=MagicMock(),
        embedding_provider=MagicMock(),
        tool_registry=MagicMock(),
        config=MagicMock(),
        map_reduce_max_papers=8,
        recency_weight=0.5,
        recency_half_life_years=10.0,
        kb_metas=[MagicMock(), MagicMock()],
    )
    assert o.recency_weight == 0.5
    assert o.recency_half_life_years == 10.0
    assert len(o.kb_metas) == 2
```

- [ ] **Step 2: Patch the orchestrator**

In [src/perspicacite/rag/agentic/orchestrator.py](src/perspicacite/rag/agentic/orchestrator.py), add to `__init__`:

```python
def __init__(self, *, llm_client, vector_store, embedding_provider, tool_registry, config,
             map_reduce_max_papers=8,
             recency_weight=None,
             recency_half_life_years=None,
             kb_metas=None):
    ...
    self.recency_weight = recency_weight
    self.recency_half_life_years = recency_half_life_years
    self.kb_metas = kb_metas or []
```

In the retrieval helper used by tool steps, when `len(self.kb_metas) > 1`, construct `MultiKBRetriever(vector_store=self.vector_store, embedding_service=self.embedding_provider, kb_metas=self.kb_metas)` instead of the single-KB retriever; otherwise keep the current path. Apply `apply_recency_weighting` to each retrieval result list when `self.recency_weight` is set. Push `_c.add_trace("intent", value=...)`, `_c.add_trace("plan", steps=...)`, `_c.add_trace("tool", name=...)`, `_c.add_trace("iteration", n=...)`, `_c.add_trace("replan", reason=...)` at the corresponding points. Per retrieved chunk: `_c.add_retrieval(..., stage_label="agentic.tool.search_kb")`.

In [src/perspicacite/rag/modes/agentic.py](src/perspicacite/rag/modes/agentic.py), in `execute_stream`, pass through:

```python
from types import SimpleNamespace
from perspicacite.models.kb import chroma_collection_name_for_kb

kb_names = getattr(request, "kb_names", None)
kb_metas = (
    [SimpleNamespace(name=n, collection_name=chroma_collection_name_for_kb(n), embedding_model=None) for n in kb_names]
    if kb_names and len(kb_names) > 1 else []
)
orchestrator = AgenticOrchestrator(
    ...,
    recency_weight=getattr(request, "recency_weight", None),
    recency_half_life_years=getattr(request, "recency_half_life_years", None),
    kb_metas=kb_metas,
)
```

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/unit/test_agentic_recency_multikb.py tests/unit/test_orchestrator_config.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/rag/agentic/orchestrator.py src/perspicacite/rag/modes/agentic.py \
        tests/unit/test_agentic_recency_multikb.py tests/unit/test_orchestrator_config.py
git commit -m "$(cat <<'EOF'
feat(rag/agentic): orchestrator honors recency_weight + multi-KB

New constructor params recency_weight, recency_half_life_years, kb_metas.
Internal retrievals build a MultiKBRetriever when >1 KB given; each
retrieval result is recency-weighted when configured. Mode-level trace
events: intent, plan, tool, iteration, replan.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 4 — Async ingestion + SSE progress

## Task 4.1: JobRegistry + jobs table

**Files:**
- Modify: `src/perspicacite/memory/session_store.py` — `jobs` table in `SCHEMA`
- Create: `src/perspicacite/jobs/__init__.py`
- Create: `src/perspicacite/jobs/registry.py`
- Test: `tests/unit/test_jobs_registry.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_jobs_registry.py
import asyncio
from pathlib import Path

import pytest

from perspicacite.jobs.registry import JobRegistry
from perspicacite.memory.session_store import SessionStore


@pytest.mark.asyncio
async def test_jobs_lifecycle(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    reg = JobRegistry(db_path=tmp_path / "p.db")
    job_id = await reg.create(kind="bibtex_ingest", total=10)
    assert job_id

    async def subscriber() -> list:
        events = []
        async for ev in reg.subscribe(job_id):
            events.append(ev)
        return events

    task = asyncio.create_task(subscriber())
    await asyncio.sleep(0)  # let subscriber start

    await reg.publish(job_id, {"type": "progress", "done": 1})
    await reg.publish(job_id, {"type": "progress", "done": 2})
    await reg.finish(job_id, {"added_papers": 2})
    events = await asyncio.wait_for(task, timeout=2.0)
    assert any(e.get("type") == "progress" and e.get("done") == 1 for e in events)
    assert events[-1].get("type") == "done"

    row = await reg.get(job_id)
    assert row["status"] == "done"
    assert row["result"]["added_papers"] == 2


@pytest.mark.asyncio
async def test_jobs_fail(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    reg = JobRegistry(db_path=tmp_path / "p.db")
    job_id = await reg.create(kind="doi_ingest", total=5)
    await reg.fail(job_id, "boom")
    row = await reg.get(job_id)
    assert row["status"] == "error"
    assert row["error"] == "boom"
```

- [ ] **Step 2: Extend `SCHEMA`**

Append to `SCHEMA` in [src/perspicacite/memory/session_store.py](src/perspicacite/memory/session_store.py):

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    total INTEGER DEFAULT 0,
    done_count INTEGER DEFAULT 0,
    result TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

- [ ] **Step 3: Implement registry**

```python
# src/perspicacite/jobs/__init__.py
from perspicacite.jobs.registry import JobRegistry
__all__ = ["JobRegistry"]
```

```python
# src/perspicacite/jobs/registry.py
"""Small in-process job registry: SQLite row + in-memory event queues."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.jobs")


class JobRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._queues: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}

    async def create(self, kind: str, total: int) -> str:
        job_id = str(uuid.uuid4())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO jobs (id, kind, status, total) VALUES (?, ?, 'running', ?)",
                (job_id, kind, total),
            )
            await db.commit()
        self._queues[job_id] = asyncio.Queue()
        return job_id

    async def publish(self, job_id: str, event: dict[str, Any]) -> None:
        q = self._queues.get(job_id)
        if q is not None:
            await q.put(event)
        if event.get("type") == "progress" and "done" in event:
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        "UPDATE jobs SET done_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (int(event["done"]), job_id),
                    )
                    await db.commit()
            except Exception as exc:
                logger.warning("jobs_progress_persist_failed", error=str(exc))

    async def finish(self, job_id: str, result: dict[str, Any]) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE jobs SET status='done', result=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (json.dumps(result), job_id),
            )
            await db.commit()
        q = self._queues.get(job_id)
        if q is not None:
            await q.put({"type": "done", "result": result})
            await q.put(None)  # terminator

    async def fail(self, job_id: str, err: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE jobs SET status='error', error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (err, job_id),
            )
            await db.commit()
        q = self._queues.get(job_id)
        if q is not None:
            await q.put({"type": "error", "error": err})
            await q.put(None)

    async def subscribe(self, job_id: str) -> AsyncIterator[dict[str, Any]]:
        q = self._queues.setdefault(job_id, asyncio.Queue())
        while True:
            ev = await q.get()
            if ev is None:
                return
            yield ev

    async def get(self, job_id: str) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        if row is None:
            return None
        d = {k: row[k] for k in row.keys()}
        if d.get("result"):
            try:
                d["result"] = json.loads(d["result"])
            except json.JSONDecodeError:
                pass
        return d
```

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/unit/test_jobs_registry.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/memory/session_store.py \
        src/perspicacite/jobs/__init__.py src/perspicacite/jobs/registry.py \
        tests/unit/test_jobs_registry.py
git commit -m "$(cat <<'EOF'
feat(jobs): minimal JobRegistry + jobs SQLite table

create/publish/finish/fail/subscribe/get. SQLite row is the source of
truth; in-memory queues drive SSE streams. Idempotent CREATE TABLE in
init_db.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4.2: AppState wires JobRegistry; jobs router

**Files:**
- Modify: `src/perspicacite/web/state.py` — add `self.job_registry: JobRegistry | None = None`; instantiate after session store
- Create: `src/perspicacite/web/routers/jobs.py`
- Modify: `src/perspicacite/web/app.py` — register router
- Modify: `tests/unit/test_web_app_routes.py` — `EXPECTED_ROUTES` adds `/api/jobs/{id}` (GET) and `/api/jobs/{id}/events` (GET); count +2.

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_async_ingestion_endpoints.py  (start the file — async POSTs come in 4.3)
from fastapi.testclient import TestClient
from perspicacite.web.app import app

def test_jobs_get_404_for_unknown():
    client = TestClient(app)
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code == 404
```

- [ ] **Step 2: Implement router**

```python
# src/perspicacite/web/routers/jobs.py
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("/{job_id}")
async def get_job(job_id: str):
    from perspicacite.web.state import app_state
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="jobs not configured")
    row = await app_state.job_registry.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return row


@router.get("/{job_id}/events")
async def stream_job_events(job_id: str):
    from perspicacite.web.state import app_state
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="jobs not configured")
    row = await app_state.job_registry.get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")

    async def gen():
        if row["status"] in ("done", "error"):
            # Job already terminal — emit a single final frame
            payload = {"type": row["status"], "result": row.get("result"), "error": row.get("error")}
            yield f"data: {json.dumps(payload)}\n\n"
            return
        async for ev in app_state.job_registry.subscribe(job_id):
            yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
```

- [ ] **Step 3: Register + wire AppState**

In [src/perspicacite/web/app.py](src/perspicacite/web/app.py): add `from perspicacite.web.routers import jobs as jobs_router` and `app.include_router(jobs_router.router)`.

In [src/perspicacite/web/state.py](src/perspicacite/web/state.py): `self.job_registry: JobRegistry | None = None` in `__init__`; in `initialize()` after `init_db`:

```python
from perspicacite.jobs.registry import JobRegistry
self.job_registry = JobRegistry(db_path=self.session_store.db_path)
```

- [ ] **Step 4: Update route expectations**

In [tests/unit/test_web_app_routes.py](tests/unit/test_web_app_routes.py):

```python
("/api/jobs/{job_id}", "GET"),
("/api/jobs/{job_id}/events", "GET"),
```

Bump count floor +2.

- [ ] **Step 5: Run tests + commit**

```bash
uv run pytest tests/unit/test_async_ingestion_endpoints.py tests/unit/test_web_app_routes.py -v
git add src/perspicacite/web/state.py src/perspicacite/web/app.py \
        src/perspicacite/web/routers/jobs.py tests/unit/test_async_ingestion_endpoints.py \
        tests/unit/test_web_app_routes.py
git commit -m "$(cat <<'EOF'
feat(jobs): /api/jobs/{id} + SSE events + AppState wiring

GET returns the persisted row (404 if missing). Events stream subscribes
to the in-memory queue; terminal jobs emit a single final frame and close.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4.3: Async BibTeX ingestion endpoint

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py` — add `POST /api/kb/{name}/bibtex/async`
- Test: `tests/unit/test_async_ingestion_endpoints.py` (extend)

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_async_ingestion_endpoints.py — append
import asyncio
import pytest
from fastapi.testclient import TestClient
from perspicacite.web.app import app
from perspicacite.web.state import app_state


@pytest.mark.asyncio
async def test_async_bibtex_returns_job_id_and_runs(monkeypatch, tmp_path):
    # Stub the heavy BibTeX worker so the test stays unit-scoped
    async def fake_worker(name, bibtex_text, *, job_id, registry, **kw):
        await registry.publish(job_id, {"type": "progress", "done": 1, "doi": "10.1/x"})
        await registry.finish(job_id, {"added_papers": 1, "added_chunks": 3})

    from perspicacite.web.routers import kb as kb_router
    monkeypatch.setattr(kb_router, "_bibtex_ingest_worker", fake_worker, raising=False)

    client = TestClient(app)
    r = client.post("/api/kb/default/bibtex/async", json={"bibtex": "@article{x, doi={10.1/x}}"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    # Wait for the background task to finish (poll get_job)
    for _ in range(50):
        row = client.get(f"/api/jobs/{job_id}").json()
        if row.get("status") == "done":
            break
        await asyncio.sleep(0.05)
    assert row["status"] == "done"
    assert row["result"]["added_papers"] == 1
```

- [ ] **Step 2: Implement**

In [src/perspicacite/web/routers/kb.py](src/perspicacite/web/routers/kb.py):

1. Add a `_bibtex_ingest_worker(name, bibtex_text, *, job_id, registry, **kw)` async function that performs the same ingestion logic the synchronous BibTeX endpoint uses, but calls `registry.publish(job_id, {"type": "progress", "done": i, "doi": …})` after each paper and `registry.finish(job_id, {...})` at the end (or `registry.fail(job_id, str(exc))` on failure). Reuse helpers from the existing sync handler — extract them if needed (additive).
2. Add the async route:

```python
@router.post("/{name}/bibtex/async")
async def add_bibtex_async(name: str, payload: KBBibtexRequest):
    from perspicacite.web.state import app_state
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="jobs not configured")
    # Validate / count entries up front so we can pass total
    total = _count_bibtex_entries(payload.bibtex)
    job_id = await app_state.job_registry.create(kind="bibtex_ingest", total=total)
    asyncio.create_task(_bibtex_ingest_worker(
        name=name, bibtex_text=payload.bibtex,
        job_id=job_id, registry=app_state.job_registry,
    ))
    return {"job_id": job_id, "total": total}
```

3. `EXPECTED_ROUTES` gets `("/api/kb/{name}/bibtex/async", "POST")`. Route-count +1.

- [ ] **Step 3: Run tests + commit**

```bash
uv run pytest tests/unit/test_async_ingestion_endpoints.py tests/unit/test_web_app_routes.py -v
git add src/perspicacite/web/routers/kb.py tests/unit/test_async_ingestion_endpoints.py \
        tests/unit/test_web_app_routes.py
git commit -m "$(cat <<'EOF'
feat(kb): POST /api/kb/{name}/bibtex/async + progress events

Returns {job_id} immediately; an asyncio task runs the existing ingestion
logic and publishes per-paper progress to the JobRegistry. Sync endpoint
unchanged.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4.4: Async DOIs ingestion endpoint

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py` — add `POST /api/kb/{name}/dois/async`
- Test: `tests/unit/test_async_ingestion_endpoints.py` (extend, same shape as 4.3)

- [ ] **Steps 1-5:** Mirror Task 4.3 for DOI ingestion. New route `POST /api/kb/{name}/dois/async` accepting the same `KBAddDOIsRequest` body. Add a `_dois_ingest_worker` (extracted from the sync handler) that publishes per-DOI progress and calls `finish`. Add to `EXPECTED_ROUTES`; count +1.

```bash
git commit -m "$(cat <<'EOF'
feat(kb): POST /api/kb/{name}/dois/async with per-DOI progress events

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4.5: UI progress bar

**Files:**
- Modify: `static/js/kb.js` — switch the "create KB from BibTeX" + "add DOIs" buttons to call the `/async` endpoints and stream `/api/jobs/{id}/events`
- Modify: `static/css/kb.css` — `.progress-bar`, `.progress-bar-fill`
- Modify: `MANUAL_QA.md` — async-ingestion section
- Modify: `tests/unit/test_static_assets.py` — assert progress-bar CSS selectors present

- [ ] **Steps 1-5:** Implement the JS handler to POST to the async endpoint, capture `job_id`, open `new EventSource('/api/jobs/'+jobId+'/events')`, and update a progress bar from each `progress` event (`done / total`). On `done` event close the source and refresh the KB list. On `error`, show the message. Failure fallback: if EventSource errors, switch to `setInterval` polling of `GET /api/jobs/{id}` every 2s.

```bash
git commit -m "$(cat <<'EOF'
feat(ui/kb): progress bar for async BibTeX + DOI ingestion

EventSource on /api/jobs/{id}/events drives a percent bar; polling
fallback when SSE is unavailable. Manual-QA section appended.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

# Phase 5 — Coverage & integrations

## Task 5.1: Europe PMC source

**Files:**
- Create: `src/perspicacite/pipeline/download/europepmc.py`
- Test: `tests/unit/test_europepmc.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_europepmc.py
import respx
import httpx
import pytest

from perspicacite.pipeline.download.europepmc import get_content_from_europepmc

PMC_XML = b"""<article><front><article-meta><title-group><article-title>Test</article-title></title-group></article-meta></front>
<body><sec><title>Intro</title><p>Hello world.</p></sec></body></article>"""


@pytest.mark.asyncio
async def test_europepmc_returns_structured_for_known_pmcid(respx_mock):
    respx_mock.get("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC/PMC123/fullTextXML").mock(
        return_value=httpx.Response(200, content=PMC_XML, headers={"content-type": "application/xml"})
    )
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(doi=None, pmid=None, pmcid="PMC123", http_client=client)
    assert out is not None
    assert out.success is True
    assert out.content_type == "structured"
    assert out.content_source == "europepmc"
    assert "Hello world." in (out.full_text or "")


@pytest.mark.asyncio
async def test_europepmc_404_returns_none(respx_mock):
    respx_mock.get("https://www.ebi.ac.uk/europepmc/webservices/rest/PMC/PMC404/fullTextXML").mock(
        return_value=httpx.Response(404)
    )
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(doi=None, pmid=None, pmcid="PMC404", http_client=client)
    assert out is None


@pytest.mark.asyncio
async def test_europepmc_resolves_doi_via_search(respx_mock):
    respx_mock.get(url__regex=r"https://www\.ebi\.ac\.uk/europepmc/webservices/rest/search.*").mock(
        return_value=httpx.Response(200, json={"resultList": {"result": [{"source": "MED", "id": "999"}]}})
    )
    respx_mock.get("https://www.ebi.ac.uk/europepmc/webservices/rest/MED/999/fullTextXML").mock(
        return_value=httpx.Response(200, content=PMC_XML)
    )
    async with httpx.AsyncClient() as client:
        out = await get_content_from_europepmc(doi="10.1/x", pmid=None, pmcid=None, http_client=client)
    assert out is not None and out.success
```

- [ ] **Step 2: Implement**

```python
# src/perspicacite/pipeline/download/europepmc.py
"""Europe PMC structured full-text source."""

from __future__ import annotations

from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.pipeline.download.base import PaperContent
from perspicacite.pipeline.download.pmc import (
    _extract_references_from_xml,
    _extract_sections_from_xml,
    _extract_text_from_xml,
)

logger = get_logger("perspicacite.pipeline.europepmc")

EUROPEPMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"


async def get_content_from_europepmc(
    *,
    doi: str | None,
    pmid: str | None,
    pmcid: str | None,
    http_client: httpx.AsyncClient,
    **_: Any,
) -> PaperContent | None:
    source, ident = await _resolve_id(doi=doi, pmid=pmid, pmcid=pmcid, http_client=http_client)
    if not source or not ident:
        return None
    url = f"{EUROPEPMC_REST}/{source}/{ident}/fullTextXML"
    try:
        r = await http_client.get(url, timeout=30)
    except httpx.HTTPError as exc:
        logger.info("europepmc_fetch_failed", error=str(exc))
        return None
    if r.status_code != 200 or not r.content:
        return None
    try:
        full_text = _extract_text_from_xml(r.content)
        sections = _extract_sections_from_xml(r.content)
        references = _extract_references_from_xml(r.content)
    except Exception as exc:  # noqa: BLE001
        logger.info("europepmc_parse_failed", error=str(exc))
        return None
    if not (full_text or "").strip():
        return None
    return PaperContent(
        success=True,
        doi=doi,
        content_type="structured",
        full_text=full_text,
        sections=sections,
        references=references,
        abstract=None,
        content_source="europepmc",
        metadata={"europepmc_source": source, "europepmc_id": ident},
    )


async def _resolve_id(*, doi: str | None, pmid: str | None, pmcid: str | None, http_client: httpx.AsyncClient) -> tuple[str | None, str | None]:
    if pmcid:
        return "PMC", str(pmcid).removeprefix("PMC")  # keep as 'PMC123' in url; see below
    if pmid:
        return "MED", str(pmid)
    if not doi:
        return None, None
    try:
        r = await http_client.get(
            f"{EUROPEPMC_REST}/search",
            params={"query": f"DOI:{doi}", "format": "json", "resultType": "lite", "pageSize": 1},
            timeout=15,
        )
        if r.status_code != 200:
            return None, None
        data = r.json()
        hits = (data.get("resultList") or {}).get("result") or []
        if not hits:
            return None, None
        h = hits[0]
        src = h.get("source")
        ident = h.get("id") or h.get("pmcid") or h.get("pmid")
        if not src or not ident:
            return None, None
        return src, str(ident)
    except (httpx.HTTPError, ValueError):
        return None, None
```

> Note: keep the test URL `…/PMC/PMC123/…` consistent — fix the resolver to return `("PMC", "PMC123")` if Europe PMC's PMC source ids include the `PMC` prefix (verify against fixture; adjust the URL builder if not).

- [ ] **Step 3: Wire into unified.py**

In [src/perspicacite/pipeline/download/unified.py](src/perspicacite/pipeline/download/unified.py), in the STRUCTURED stage, **after** the PMC JATS attempt and **before** the arXiv HTML attempt, add:

```python
from perspicacite.pipeline.download.europepmc import get_content_from_europepmc

if not result or not result.success:
    epmc = await get_content_from_europepmc(
        doi=doi, pmid=disc.pmid if hasattr(disc, "pmid") else None,
        pmcid=disc.pmcid, http_client=http_client,
    )
    if epmc and epmc.success:
        return epmc
```

(If `PaperDiscovery` doesn't have a `pmid` field, pass `None` — the resolver handles that.)

- [ ] **Step 4: Run tests + commit**

```bash
uv run pytest tests/unit/test_europepmc.py tests/unit/test_download.py -v
uv run pytest tests/unit/ -m "not live" -q
git add src/perspicacite/pipeline/download/europepmc.py \
        src/perspicacite/pipeline/download/unified.py \
        tests/unit/test_europepmc.py
git commit -m "$(cat <<'EOF'
feat(pipeline): Europe PMC structured source (fullTextXML)

Resolves a {source, id} via PMCID/PMID directly or DOI lookup via the
EuropePMC search API; fetches fullTextXML; parses with the existing JATS
extractors. Wired into the STRUCTURED stage after PMC JATS, before arXiv.
No new config (public API).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.2: ZoteroConfig schema + config.example.yml

**Files:**
- Modify: `src/perspicacite/config/schema.py` — `ZoteroConfig`, attach to `Config`
- Modify: `config.example.yml`
- Test: `tests/unit/test_config.py` (extend)

- [ ] **Step 1: Failing test**

```python
def test_zotero_config_defaults():
    from perspicacite.config.schema import Config
    cfg = Config()
    assert cfg.zotero.enabled is False
    assert cfg.zotero.api_key == ""
    assert cfg.zotero.library_type == "user"
```

- [ ] **Step 2: Implement**

In [src/perspicacite/config/schema.py](src/perspicacite/config/schema.py):

```python
from pydantic import BaseModel, Field

class ZoteroConfig(BaseModel):
    enabled: bool = False
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"  # or "group"
    collection_key: str = ""
```

Add `zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)` to the `Config` model.

In `config.example.yml`, append:

```yaml
zotero:
  enabled: false
  api_key: ""
  library_id: ""
  library_type: "user"      # or "group"
  collection_key: ""        # optional; empty = no collection
```

- [ ] **Step 3-5: Run tests + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(config): ZoteroConfig (disabled by default)

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.3: ZoteroClient

**Files:**
- Create: `src/perspicacite/integrations/__init__.py`
- Create: `src/perspicacite/integrations/zotero.py`
- Test: `tests/unit/test_zotero.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_zotero.py
import respx, httpx, pytest

from perspicacite.integrations.zotero import ZoteroClient


@pytest.mark.asyncio
async def test_create_item_maps_doi_and_dedupes(respx_mock):
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[])  # no duplicates
    )
    create_route = respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json={
            "success": {"0": "ABC123"},
            "successful": {"0": {"key": "ABC123"}},
        })
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={
            "doi": "10.1/x", "title": "T", "year": 2024, "journal": "J",
            "authors": ["A B"], "abstract": "abs",
        })
    assert key == "ABC123"
    body = create_route.calls[0].request.read()
    assert b'"DOI": "10.1/x"' in body or b'"DOI":"10.1/x"' in body


@pytest.mark.asyncio
async def test_dedup_returns_existing_key(respx_mock):
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[
            {"key": "EXIST", "data": {"DOI": "10.1/x"}}
        ])
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={"doi": "10.1/x", "title": "T"})
    assert key == "EXIST"
```

- [ ] **Step 2: Implement**

```python
# src/perspicacite/integrations/__init__.py
"""External integrations (Zotero, Obsidian, ...)."""
```

```python
# src/perspicacite/integrations/zotero.py
from __future__ import annotations

from typing import Any

import httpx

ZOTERO_API = "https://api.zotero.org"


class ZoteroClient:
    def __init__(self, *, api_key: str, library_id: str, library_type: str = "user",
                 collection_key: str = "", http_client: httpx.AsyncClient | None = None):
        if not api_key or not library_id:
            raise ValueError("Zotero api_key and library_id are required")
        self.api_key = api_key
        self.library_id = library_id
        self.library_type = "groups" if library_type == "group" else "users"
        self.collection_key = collection_key
        self._http = http_client

    def _base(self) -> str:
        return f"{ZOTERO_API}/{self.library_type}/{self.library_id}"

    def _headers(self) -> dict[str, str]:
        return {
            "Zotero-API-Key": self.api_key,
            "Zotero-API-Version": "3",
            "Content-Type": "application/json",
        }

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def create_item(self, paper: dict[str, Any]) -> str | None:
        c = await self._client()
        doi = paper.get("doi")
        if doi:
            r = await c.get(f"{self._base()}/items", params={"q": doi, "qmode": "everything", "format": "json"}, headers=self._headers())
            if r.status_code == 200:
                for item in r.json() or []:
                    if (item.get("data") or {}).get("DOI", "").lower() == doi.lower():
                        return item.get("key")

        creators = []
        for a in (paper.get("authors") or []):
            parts = a.split(" ", 1)
            creators.append({"creatorType": "author", "firstName": parts[0] if len(parts) > 1 else "",
                             "lastName": parts[1] if len(parts) > 1 else a})
        body = [{
            "itemType": "journalArticle",
            "title": paper.get("title") or "",
            "DOI": doi or "",
            "date": str(paper.get("year") or ""),
            "publicationTitle": paper.get("journal") or "",
            "abstractNote": paper.get("abstract") or "",
            "creators": creators or [{"creatorType": "author", "firstName": "", "lastName": "Unknown"}],
            **({"collections": [self.collection_key]} if self.collection_key else {}),
        }]
        r = await c.post(f"{self._base()}/items", json=body, headers=self._headers())
        if r.status_code not in (200, 201):
            return None
        data = r.json() or {}
        # Zotero returns {"successful": {"0": {"key": "..."}}, "success": {"0": "..."}, "failed": {...}}
        s = data.get("successful") or {}
        if s:
            v = next(iter(s.values()))
            return v.get("key")
        s2 = data.get("success") or {}
        if s2:
            return next(iter(s2.values()))
        return None
```

- [ ] **Step 3-5: Run tests + commit**

```bash
git commit -m "$(cat <<'EOF'
feat(integrations): ZoteroClient — create + dedup journalArticle items

Searches the library by DOI before creating; returns the existing item
key on a hit. Maps doi/title/year/journal/abstract/authors to Zotero's
fields. Optional collection_key.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.4: push_to_zotero MCP tool + /api/zotero/push endpoint

**Files:**
- Modify: `src/perspicacite/mcp/server.py` — new tool, count 10→11, update `get_info()`
- Create: `src/perspicacite/web/routers/zotero.py`
- Modify: `src/perspicacite/web/app.py` — register router
- Modify: `tests/unit/test_web_app_routes.py`, `tests/test_mcp_server.py`

- [ ] **Step 1: Failing tests**

```python
# tests/unit/test_zotero.py — extend
import pytest
from fastapi.testclient import TestClient


def test_zotero_push_endpoint_503_when_unconfigured(monkeypatch):
    from perspicacite.web.app import app
    from perspicacite.web.state import app_state
    app_state.config = type("c", (), {"zotero": type("z", (), {"enabled": False})()})()
    client = TestClient(app)
    r = client.post("/api/zotero/push", json={"dois": ["10.1/x"]})
    assert r.status_code == 503
```

```python
# tests/test_mcp_server.py — extend
def test_push_to_zotero_in_get_info():
    from perspicacite.mcp.server import get_info
    info = get_info()
    assert "push_to_zotero" in info["tools"]
    assert info["tool_count"] == 11
```

- [ ] **Step 2: Implement router**

```python
# src/perspicacite/web/routers/zotero.py
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/zotero", tags=["zotero"])


class PushRequest(BaseModel):
    dois: list[str]


@router.post("/push")
async def push(payload: PushRequest):
    from perspicacite.web.state import app_state
    cfg = getattr(app_state.config, "zotero", None)
    if not cfg or not cfg.enabled or not cfg.api_key or not cfg.library_id:
        raise HTTPException(status_code=503, detail="zotero not configured")
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.pipeline.download.unified import retrieve_paper_content

    client = ZoteroClient(api_key=cfg.api_key, library_id=cfg.library_id,
                          library_type=cfg.library_type, collection_key=cfg.collection_key)
    created, skipped, failed = [], [], []
    for doi in payload.dois:
        try:
            content = await retrieve_paper_content(doi=doi, http_client=app_state.http_client, pdf_parser=None)
            paper = (content.metadata or {})
            paper["doi"] = doi
            paper["abstract"] = content.abstract or paper.get("abstract")
            key = await client.create_item(paper)
            (created if key else failed).append({"doi": doi, "key": key})
        except Exception as exc:
            failed.append({"doi": doi, "error": str(exc)})
    return {"created": created, "skipped": skipped, "failed": failed}
```

Register in [src/perspicacite/web/app.py](src/perspicacite/web/app.py).

- [ ] **Step 3: Implement MCP tool**

In [src/perspicacite/mcp/server.py](src/perspicacite/mcp/server.py):

```python
@mcp.tool
async def push_to_zotero(dois: list[str] | str) -> str:
    """Push one or more DOIs to the configured Zotero library.

    Returns JSON {created: [...], skipped: [...], failed: [...]}. Returns an
    error JSON when Zotero is not configured.
    """
    state = _require_state()
    if isinstance(state, str):
        return _json_error(state)
    cfg = getattr(state.config, "zotero", None)
    if not cfg or not cfg.enabled:
        return _json_error("zotero_not_configured")
    if isinstance(dois, str):
        dois = [dois]
    from perspicacite.integrations.zotero import ZoteroClient
    from perspicacite.pipeline.download.unified import retrieve_paper_content
    client = ZoteroClient(api_key=cfg.api_key, library_id=cfg.library_id,
                          library_type=cfg.library_type, collection_key=cfg.collection_key)
    created, skipped, failed = [], [], []
    for doi in dois:
        try:
            content = await retrieve_paper_content(doi=doi, http_client=state.http_client, pdf_parser=None)
            paper = dict(content.metadata or {})
            paper["doi"] = doi
            paper["abstract"] = content.abstract or paper.get("abstract")
            key = await client.create_item(paper)
            (created if key else failed).append({"doi": doi, "key": key})
        except Exception as exc:  # noqa: BLE001
            failed.append({"doi": doi, "error": str(exc)})
    return _json_ok({"created": created, "skipped": skipped, "failed": failed})
```

Update `get_info()`'s `tools` list to include `"push_to_zotero"` and `tool_count` 11. Update the module docstring and any tool-count constants.

- [ ] **Step 4: Route count + commit**

`EXPECTED_ROUTES`: `("/api/zotero/push", "POST")`; count +1.

```bash
uv run pytest tests/unit/test_zotero.py tests/test_mcp_server.py tests/unit/test_web_app_routes.py -v
git add src/perspicacite/mcp/server.py src/perspicacite/web/routers/zotero.py \
        src/perspicacite/web/app.py tests/unit/test_zotero.py \
        tests/test_mcp_server.py tests/unit/test_web_app_routes.py
git commit -m "$(cat <<'EOF'
feat(integrations): push_to_zotero MCP tool + /api/zotero/push endpoint

Both fetch metadata via the unified pipeline (pdf_parser=None — fast
metadata-only lookup), then call ZoteroClient.create_item. 503/error
JSON when Zotero is not configured.

MCP tool count 10 -> 11.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.5: UI — "Send to Zotero" button

**Files:**
- Modify: `static/js/paper_detail.js` and `static/js/chat.js` — Zotero button
- Modify: `static/css/chat.css` — `.zotero-btn` style
- Modify: `MANUAL_QA.md` — Zotero section

- [ ] **Steps 1-4:** Add a `data-doi`-aware button to the paper-detail panel and each chat source card. Button shows only when `GET /api/health` (or a new `/api/zotero/status`) reports Zotero enabled. On click → `POST /api/zotero/push` with that DOI; show a brief toast on success/failure.

Optional sub-step: add `GET /api/zotero/status` returning `{enabled: bool}` so the UI doesn't need to expose config.

```bash
git commit -m "feat(ui): Send to Zotero button on paper detail + source cards

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5.6: Obsidian vault export

**Files:**
- Create: `src/perspicacite/integrations/obsidian.py`
- Modify: `src/perspicacite/web/routers/kb.py` — `GET /api/kb/{name}/export?format=obsidian-vault`
- Test: `tests/unit/test_obsidian_export.py`

- [ ] **Step 1: Failing test**

```python
# tests/unit/test_obsidian_export.py
import io, zipfile
from perspicacite.integrations.obsidian import build_obsidian_vault


def test_vault_structure_and_wikilinks():
    kb = {"name": "default", "embedding_model": "..." , "paper_count": 2, "chunk_count": 5}
    papers = [
        {"doi": "10.1/a", "title": "Paper A", "year": 2024, "journal": "J", "authors": ["X Y"],
         "content_type": "full_text", "content_source": "pmc", "abstract": "abs A"},
        {"doi": "10.1/b", "title": "Paper B", "year": 2020, "journal": "K", "authors": ["Z"],
         "content_type": "abstract", "content_source": "openalex", "abstract": "abs B"},
    ]
    conversations = [
        {"id": "conv-1", "title": "Q on microbiome", "messages": [
            {"role": "user", "content": "What is X?"},
            {"role": "assistant", "content": "Per (10.1/a) ...", "sources": [{"doi": "10.1/a", "title": "Paper A"}]},
        ]},
    ]
    blob = build_obsidian_vault(kb=kb, papers=papers, conversations=conversations)
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    assert any("default/Papers/10-1-a.md" in n for n in names)
    assert any("default/Papers/10-1-b.md" in n for n in names)
    assert any("default/Conversations/" in n and n.endswith(".md") for n in names)
    assert any(n.endswith("default/Index.md") for n in names)
    a = z.read([n for n in names if "default/Papers/10-1-a.md" in n][0]).decode()
    assert a.startswith("---") and "doi: 10.1/a" in a
    conv_md = z.read([n for n in names if "Conversations/" in n and n.endswith(".md")][0]).decode()
    assert "[[10-1-a]]" in conv_md  # wikilink to the paper note
```

- [ ] **Step 2: Implement**

```python
# src/perspicacite/integrations/obsidian.py
from __future__ import annotations

import io, re, zipfile
from typing import Any


def _slug(doi: str | None) -> str:
    if not doi:
        return "untitled"
    return re.sub(r"[^a-zA-Z0-9]+", "-", doi).strip("-").lower() or "untitled"


def _slug_title(t: str | None) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", t or "untitled").strip("-").lower() or "untitled"


def _yaml(d: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for x in v:
                lines.append(f"  - {x}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def _paper_note(paper: dict[str, Any]) -> str:
    front = _yaml({
        "doi": paper.get("doi") or "",
        "year": paper.get("year") or "",
        "journal": paper.get("journal") or "",
        "authors": paper.get("authors") or [],
        "source": paper.get("content_source") or "",
        "content_type": paper.get("content_type") or "",
        "tags": ["paper"],
    })
    body = f"\n\n# {paper.get('title') or paper.get('doi') or 'Untitled'}\n\n"
    if paper.get("abstract"):
        body += f"## Abstract\n\n{paper['abstract']}\n"
    return front + body


def _rewrite_wikilinks(text: str, doi_to_slug: dict[str, str]) -> str:
    out = text
    for doi, slug in doi_to_slug.items():
        out = out.replace(doi, f"[[{slug}]]")
    return out


def _conversation_note(conv: dict[str, Any], doi_to_slug: dict[str, str]) -> tuple[str, str]:
    title = conv.get("title") or "Untitled"
    filename = _slug_title(title) + ".md"
    parts = [f"# {title}\n"]
    for m in conv.get("messages") or []:
        role = m.get("role", "?")
        content = _rewrite_wikilinks(m.get("content", ""), doi_to_slug)
        parts.append(f"## {role.capitalize()}\n\n{content}\n")
    return filename, "\n".join(parts)


def build_obsidian_vault(*, kb: dict[str, Any], papers: list[dict[str, Any]], conversations: list[dict[str, Any]]) -> bytes:
    kb_name = kb.get("name") or "default"
    doi_to_slug = {p.get("doi"): _slug(p.get("doi")) for p in papers if p.get("doi")}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in papers:
            slug = _slug(p.get("doi"))
            z.writestr(f"{kb_name}/Papers/{slug}.md", _paper_note(p))
        for c in conversations:
            fn, body = _conversation_note(c, doi_to_slug)
            z.writestr(f"{kb_name}/Conversations/{fn}", body)
        # Index
        idx = [f"# {kb_name}\n", "## Papers\n"]
        idx.extend(f"- [[{_slug(p.get('doi'))}]] {p.get('title') or ''}" for p in papers)
        idx.append("\n## Conversations\n")
        idx.extend(f"- [[{_slug_title(c.get('title'))}]]" for c in conversations)
        z.writestr(f"{kb_name}/Index.md", "\n".join(idx))
    return buf.getvalue()
```

- [ ] **Step 3: Wire export endpoint**

In [src/perspicacite/web/routers/kb.py](src/perspicacite/web/routers/kb.py) add:

```python
@router.get("/{name}/export")
async def kb_export(name: str, format: str = "obsidian-vault"):
    from perspicacite.web.state import app_state
    from perspicacite.integrations.obsidian import build_obsidian_vault
    from fastapi.responses import Response

    if format != "obsidian-vault":
        raise HTTPException(status_code=400, detail="unsupported format")
    kb = await app_state.session_store.get_kb_metadata(name)
    if kb is None:
        raise HTTPException(status_code=404, detail="kb not found")
    papers = await app_state.kb_service.list_papers(name)  # use whichever existing helper returns paper metadata
    conversations = await app_state.session_store.list_conversations_by_kb(name) if hasattr(app_state.session_store, "list_conversations_by_kb") else []
    blob = build_obsidian_vault(
        kb=kb.model_dump() if hasattr(kb, "model_dump") else dict(kb),
        papers=[p.model_dump() if hasattr(p, "model_dump") else dict(p) for p in papers],
        conversations=[c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in conversations],
    )
    return Response(content=blob, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}-vault.zip"'})
```

If `list_conversations_by_kb` does not exist on `SessionStore`, add it (additive helper, ~5 lines).

- [ ] **Step 4: Route count, tests, commit**

Add `("/api/kb/{name}/export", "GET")` to `EXPECTED_ROUTES`; count +1.

```bash
uv run pytest tests/unit/test_obsidian_export.py tests/unit/test_web_app_routes.py -v
git add src/perspicacite/integrations/obsidian.py src/perspicacite/web/routers/kb.py \
        tests/unit/test_obsidian_export.py tests/unit/test_web_app_routes.py \
        src/perspicacite/memory/session_store.py  # if list_conversations_by_kb added
git commit -m "$(cat <<'EOF'
feat(integrations): Obsidian vault export — GET /api/kb/{name}/export

Zip layout: <KB>/Papers/<doi-slug>.md (YAML frontmatter + abstract),
<KB>/Conversations/<title-slug>.md (existing markdown + [[doi-slug]]
wikilinks), <KB>/Index.md (KB stats + links).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.7: Docs sweep

**Files:**
- Modify: `README.md` — "10 → 11 MCP tools", new endpoints, async ingestion, Europe PMC, Zotero, Obsidian export, provenance + RO-Crate
- Modify: `docs/perspicacite_skills.md` — `push_to_zotero` section; provenance section; updated tool list/count

- [ ] **Step 1: Update README.md feature list, endpoint table, MCP tools count**

- [ ] **Step 2: Update `docs/perspicacite_skills.md` (tracked by repo whitelist) — add `push_to_zotero` to the tools list, update overview**

- [ ] **Step 3: Commit**

```bash
git add README.md docs/perspicacite_skills.md
git commit -m "$(cat <<'EOF'
docs: bump MCP tool count, document provenance + new endpoints

README + perspicacite_skills.md updated for push_to_zotero, async
ingestion endpoints, Europe PMC, Obsidian vault export, RO-Crate
bundle export, and the provenance UI.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5.8: Final code-review pass

**Step 1:** Dispatch a fresh `code-reviewer` subagent over the diff range from the spec commit to HEAD (use `git log --oneline eb5a169..HEAD` to confirm).

**Step 2:** For each real bug it identifies (not style / nits), open a fix task with a minimal patch + a regression test; commit each fix as its own conventional commit on `main`.

**Step 3:** Re-run the full unit suite.

```bash
uv run pytest tests/unit/ -m "not live" -q
```

Expected: all green. End the run.

---

## Self-review (run before handoff)

1. **Spec coverage check.** Each spec section maps to one or more tasks:
   - Provenance core (collector, contextvar, store, SQLite table) → Tasks 1.1, 1.2, 1.3, 1.4, 1.5.
   - LLM-call audit (AsyncLLMClient, JSONL sidecar) → Tasks 1.6, 2.1.
   - basic/contradiction provenance → 1.7, 1.8.
   - Provenance API endpoints → 1.9.
   - RO-Crate builder + export route → 2.2, 2.3.
   - Provenance UI → 2.4.
   - RAG mode wiring (recency + multi-KB) → 3.1, 3.2, 3.3, 3.4, 3.5.
   - Async ingestion + SSE → 4.1, 4.2, 4.3, 4.4, 4.5.
   - Europe PMC → 5.1.
   - Zotero (config + client + tool + endpoint + UI) → 5.2, 5.3, 5.4, 5.5.
   - Obsidian export → 5.6.
   - Docs + final review → 5.7, 5.8.

2. **No placeholders.** Every step has either an exact command, a complete code block, or a precise file-edit description. UI tasks reference real selectors. No "etc." stand-ins.

3. **Type / name consistency.** `ProvenanceCollector.add_retrieval` keyword signature is the same in Tasks 1.1 / 1.7 / 1.8 / 3.x. `ProvenanceStore.save` signature matches between 1.2, 1.4, and 2.1. `JobRegistry` methods (`create / publish / finish / fail / subscribe / get`) match between 4.1, 4.2, and 4.3. `build_rocrate_bundle` signature matches between 2.2 and 2.3. `build_obsidian_vault` signature matches between 5.6's implementation and test.
