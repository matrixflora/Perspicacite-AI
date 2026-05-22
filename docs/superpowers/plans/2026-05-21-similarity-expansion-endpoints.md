# Similarity Expansion — Plan 3 of 4: REST Endpoints

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose the Plan 2 orchestrator over HTTP: `POST /api/kb/{name}/expand-similar/score` (SSE job → score report), `/expand-similar/cutoff` (sync → cutoff from sample labels), and `/expand-similar/commit` (SSE job → ingest kept).

**Architecture:** Three thin endpoints in the existing `web/routers/kb.py`, following its established job pattern (`job_registry.create` → `asyncio.create_task(_runner)` that calls `finish`/`fail` → return `{job_id, sse_url}`). The score report rides the job's `finish` result over the existing `/api/jobs/{id}/events` SSE stream. `cutoff` is a fast synchronous endpoint reusing Plan 1's `cutoff_from_labels`.

**Tech Stack:** FastAPI, `pytest` + `fastapi.testclient.TestClient`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-21-similarity-expansion-design.md`. **Depends on:** Plan 1 (`ae9c5d4`), Plan 2 (`3db6e68`).

> **Roadmap:** Plan 1 ✅, Plan 2 ✅. Plan 3 (this) = REST endpoints. Plan 4 = frontend page. (Plan 3 was split from the frontend so each is self-contained; the API is exercisable via curl on its own.)

> **WSL note:** `uv run pytest` has a slow (~minutes) import cost here.

---

## File Structure

- **Modify:** `src/perspicacite/web/routers/kb.py` — add three request models + three endpoints (uses the module-level `app_state`, `_local_tasks`, `asyncio`, `HTTPException`, `BaseModel` already imported there).
- **Test:** `tests/unit/test_expand_similar_endpoints.py` — TestClient tests (mocked orchestrator + job registry), mirroring `tests/unit/test_zotero_ingest_router.py`.

Reused unchanged: `pipeline/similarity_expansion.py` (`score_expansion_candidates`, `commit_expansion`), `search/screening.py` (`ScreenResult`, `cutoff_from_labels`), `jobs/registry.py`, `web/routers/jobs.py` (the SSE `/api/jobs/{id}/events`).

---

### Task 1: `/expand-similar/cutoff` (sync) + request models

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_expand_similar_endpoints.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_expand_similar_endpoints.py`:

```python
"""Endpoints for similarity expansion: /score, /cutoff, /commit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


def _state(*, has_registry=True, kb_exists=True):
    return SimpleNamespace(
        job_registry=(
            SimpleNamespace(create=AsyncMock(return_value="J1"),
                            finish=AsyncMock(), fail=AsyncMock())
            if has_registry else None
        ),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(
                return_value=SimpleNamespace(collection_name="c") if kb_exists else None
            )
        ),
    )


def _client(monkeypatch, state):
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app as fastapi_app
    from perspicacite.web.routers import kb as kb_mod

    monkeypatch.setattr(state_mod, "app_state", state)
    monkeypatch.setattr(kb_mod, "app_state", state)
    return TestClient(fastapi_app)


def test_cutoff_clean_monotonic(monkeypatch):
    client = _client(monkeypatch, _state())
    r = client.post(
        "/api/kb/kb1/expand-similar/cutoff",
        json={"labels": [
            {"score": 0.9, "relevant": True},
            {"score": 0.7, "relevant": True},
            {"score": 0.4, "relevant": False},
            {"score": 0.2, "relevant": False},
        ]},
    )
    assert r.status_code == 200
    cut = r.json()["cutoff"]
    assert 0.4 < cut <= 0.7


def test_cutoff_empty_labels(monkeypatch):
    client = _client(monkeypatch, _state())
    r = client.post("/api/kb/kb1/expand-similar/cutoff", json={"labels": []})
    assert r.status_code == 200
    assert r.json()["cutoff"] == 0.0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -k cutoff -q`
Expected: FAIL — 404 (route not defined yet).

- [ ] **Step 3: Add request models + the `cutoff` endpoint**

In `src/perspicacite/web/routers/kb.py`, add the request models near the other `class ...Request(BaseModel)` definitions (after `KBAddDOIsRequest`, ~line 80):

```python
class ExpandSimilarScoreRequest(BaseModel):
    direction: str = "both"          # "forward" | "backward" | "both"
    max_per_seed: int = 10
    method: str = "hybrid"           # "embedding" | "bm25" | "hybrid"


class _CalibrationLabel(BaseModel):
    score: float
    relevant: bool


class ExpandSimilarCutoffRequest(BaseModel):
    labels: list[_CalibrationLabel] = Field(default_factory=list)


class _ScoredCandidate(BaseModel):
    doi: str | None = None
    score: float


class ExpandSimilarCommitRequest(BaseModel):
    scored: list[_ScoredCandidate] = Field(default_factory=list)
    cutoff: float
```

Then add the endpoint (place all three near the other `@router.post("/api/kb/{name}/...")` job endpoints, e.g. after `build_capsules_for_kb_async`):

```python
@router.post("/api/kb/{name}/expand-similar/cutoff")
async def expand_similar_cutoff(name: str, payload: ExpandSimilarCutoffRequest) -> dict:
    """Best-fit keep/drop cutoff from the user's labels on the calibration
    samples. Fast + synchronous (no job)."""
    from perspicacite.search.screening import ScreenResult, cutoff_from_labels

    labeled = [
        (ScreenResult(item={}, score=lbl.score, kept=False), lbl.relevant)
        for lbl in payload.labels
    ]
    return {"cutoff": cutoff_from_labels(labeled)}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -k cutoff -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_expand_similar_endpoints.py
git commit -m "feat(web): expand-similar cutoff endpoint + request models"
```

---

### Task 2: `/expand-similar/score` (SSE job)

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_expand_similar_endpoints.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_expand_similar_endpoints.py`:

```python
def test_score_503_without_registry(monkeypatch):
    client = _client(monkeypatch, _state(has_registry=False))
    r = client.post("/api/kb/kb1/expand-similar/score", json={})
    assert r.status_code == 503


def test_score_404_when_kb_missing(monkeypatch):
    client = _client(monkeypatch, _state(kb_exists=False))
    r = client.post("/api/kb/kb1/expand-similar/score", json={"method": "hybrid"})
    assert r.status_code == 404


def test_score_returns_job(monkeypatch):
    import perspicacite.pipeline.similarity_expansion as se

    async def _fake_score(**kwargs):
        return SimpleNamespace(candidates=[], histogram=[], samples=[], seed_count=0, method="hybrid")

    monkeypatch.setattr(se, "score_expansion_candidates", _fake_score)
    client = _client(monkeypatch, _state())
    r = client.post(
        "/api/kb/kb1/expand-similar/score",
        json={"direction": "forward", "max_per_seed": 5, "method": "embedding"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "J1"
    assert body["sse_url"] == "/api/jobs/J1/events"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -k score -q`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the `score` endpoint**

In `src/perspicacite/web/routers/kb.py`, add:

```python
@router.post("/api/kb/{name}/expand-similar/score")
async def expand_similar_score(name: str, payload: ExpandSimilarScoreRequest) -> dict:
    """Phase 1: snowball + similarity-score candidates against the KB. Returns
    a job whose SSE ``done`` event carries {candidates, histogram, samples}."""
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    kb_meta = await app_state.session_store.get_kb_metadata(name)
    if kb_meta is None:
        raise HTTPException(status_code=404, detail=f"KB '{name}' not found")
    job_id = await app_state.job_registry.create("expand_similar_score", total=1)

    async def _runner():
        from perspicacite.pipeline.similarity_expansion import score_expansion_candidates
        try:
            report = await score_expansion_candidates(
                app_state=app_state,
                kb_name=name,
                direction=payload.direction,
                max_per_seed=payload.max_per_seed,
                method=payload.method,
            )
            await app_state.job_registry.finish(job_id, {
                "candidates": report.candidates,
                "histogram": report.histogram,
                "samples": report.samples,
                "seed_count": report.seed_count,
                "method": report.method,
            })
        except Exception as exc:  # noqa: BLE001 — report failure on the stream
            await app_state.job_registry.fail(job_id, str(exc))

    task = asyncio.create_task(_runner())
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -k score -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/perspicacite/web/routers/kb.py tests/unit/test_expand_similar_endpoints.py
git commit -m "feat(web): expand-similar score endpoint (SSE job)"
```

---

### Task 3: `/expand-similar/commit` (SSE job)

**Files:**
- Modify: `src/perspicacite/web/routers/kb.py`
- Test: `tests/unit/test_expand_similar_endpoints.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_expand_similar_endpoints.py`:

```python
def test_commit_503_without_registry(monkeypatch):
    client = _client(monkeypatch, _state(has_registry=False))
    r = client.post(
        "/api/kb/kb1/expand-similar/commit",
        json={"scored": [{"doi": "10.1/x", "score": 0.9}], "cutoff": 0.5},
    )
    assert r.status_code == 503


def test_commit_404_when_kb_missing(monkeypatch):
    client = _client(monkeypatch, _state(kb_exists=False))
    r = client.post(
        "/api/kb/kb1/expand-similar/commit",
        json={"scored": [{"doi": "10.1/x", "score": 0.9}], "cutoff": 0.5},
    )
    assert r.status_code == 404


def test_commit_returns_job(monkeypatch):
    import perspicacite.pipeline.similarity_expansion as se

    async def _fake_commit(**kwargs):
        return {"added_papers": 1, "kept": 1}

    monkeypatch.setattr(se, "commit_expansion", _fake_commit)
    client = _client(monkeypatch, _state())
    r = client.post(
        "/api/kb/kb1/expand-similar/commit",
        json={"scored": [{"doi": "10.1/x", "score": 0.9}], "cutoff": 0.5},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["job_id"] == "J1"
    assert body["sse_url"] == "/api/jobs/J1/events"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -k commit -q`
Expected: FAIL — 404 (route not defined).

- [ ] **Step 3: Add the `commit` endpoint**

In `src/perspicacite/web/routers/kb.py`, add:

```python
@router.post("/api/kb/{name}/expand-similar/commit")
async def expand_similar_commit(name: str, payload: ExpandSimilarCommitRequest) -> dict:
    """Phase 2: ingest the candidates scoring at/above ``cutoff`` into the KB.
    Returns a job whose SSE ``done`` event carries the ingest report."""
    if app_state.job_registry is None:
        raise HTTPException(status_code=503, detail="Job registry not available")
    kb_meta = await app_state.session_store.get_kb_metadata(name)
    if kb_meta is None:
        raise HTTPException(status_code=404, detail=f"KB '{name}' not found")
    scored = [{"doi": c.doi, "score": c.score} for c in payload.scored]
    job_id = await app_state.job_registry.create("expand_similar_commit", total=len(scored))

    async def _runner():
        from perspicacite.pipeline.similarity_expansion import commit_expansion
        try:
            res = await commit_expansion(
                app_state=app_state, kb_name=name, scored=scored, cutoff=payload.cutoff
            )
            await app_state.job_registry.finish(job_id, res)
        except Exception as exc:  # noqa: BLE001 — report failure on the stream
            await app_state.job_registry.fail(job_id, str(exc))

    task = asyncio.create_task(_runner())
    _local_tasks.add(task)
    task.add_done_callback(_local_tasks.discard)
    return {"job_id": job_id, "sse_url": f"/api/jobs/{job_id}/events"}
```

- [ ] **Step 4: Run the full file to verify it passes**

Run: `uv run pytest tests/unit/test_expand_similar_endpoints.py -q`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/perspicacite/web/routers/kb.py tests/unit/test_expand_similar_endpoints.py
git add src/perspicacite/web/routers/kb.py tests/unit/test_expand_similar_endpoints.py
git commit -m "feat(web): expand-similar commit endpoint (SSE job)"
```

(Fix ruff findings in the new code only; leave unrelated pre-existing findings.)

---

## Self-Review

**1. Spec coverage (this plan's slice):**
- `POST /expand-similar/score` (SSE job → score report on `done`) → Task 2 ✅; report shape `{candidates, histogram, samples, seed_count, method}` matches `ExpansionScoreReport`.
- `POST /expand-similar/cutoff` (labels → cutoff via `cutoff_from_labels`) → Task 1 ✅.
- `POST /expand-similar/commit` (cutoff → `commit_expansion`, SSE job) → Task 3 ✅.
- Validation: 503 (no registry), 404 (missing KB) on both job endpoints → Tasks 2 & 3 tests ✅.
- Deferred to Plan 4: the frontend page that calls score → renders histogram + labels the samples → POSTs to cutoff → shows slider → POSTs to commit, tailing `/api/jobs/{id}/events` for both jobs.

**2. Placeholder scan:** No TBD/TODO; every step has complete code + an exact command with expected output. ✅

**3. Type consistency:** The score endpoint's `finish` payload keys (`candidates`/`histogram`/`samples`/`seed_count`/`method`) are exactly `ExpansionScoreReport`'s fields (Plan 2). `commit` builds `scored=[{doi,score}]` — the shape `commit_expansion(scored=...)` consumes (reads `doi`,`score`). `cutoff` builds `ScreenResult(item={}, score=, kept=False)` per label and calls `cutoff_from_labels` (Plan 1 signature: `Sequence[tuple[ScreenResult, bool]] -> float`). Endpoints reuse the module-level `app_state`, `_local_tasks`, `asyncio`, `HTTPException`, `BaseModel`, `Field` already imported in `kb.py`. ✅
