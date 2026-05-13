"""Unit tests for GET /api/jobs/{id} and GET /api/jobs/{id}/events."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from perspicacite.web.app import app


def test_get_job_404_unknown():
    client = TestClient(app)
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code in (404, 503)


def test_get_job_events_404_unknown():
    client = TestClient(app)
    r = client.get("/api/jobs/does-not-exist/events")
    assert r.status_code in (404, 503)


# ---------------------------------------------------------------------------
# Async BibTeX endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_bibtex_returns_job_id(monkeypatch):
    """The async endpoint validates input, creates a job, returns {job_id, total}."""
    from perspicacite.jobs.registry import JobRegistry
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.web import state as state_mod
    from perspicacite.web.routers import kb as kb_router

    captured_calls: dict = {}

    async def fake_worker(*, name, bibtex_text, job_id, registry, **kw):
        captured_calls["called"] = True
        captured_calls["name"] = name
        await registry.publish(job_id, {"type": "progress", "done": 1})
        await registry.finish(job_id, {"added_papers": 1, "added_chunks": 3})

    monkeypatch.setattr(kb_router, "_bibtex_ingest_worker", fake_worker)

    # Stub session_store + job_registry on app_state
    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "p.db"
    ss = SessionStore(db_path)
    await ss.init_db()
    jr = JobRegistry(db_path=db_path)

    # Mock the kb metadata check (worker is stubbed anyway)
    async def fake_get_kb(name):
        class _KB:
            collection_name = "kb_default"
            paper_count = 0
            chunk_count = 0

        return _KB()

    monkeypatch.setattr(ss, "get_kb_metadata", fake_get_kb)
    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)
    monkeypatch.setattr(state_mod.app_state, "job_registry", jr, raising=False)

    # Use AsyncClient so create_task runs in the same event loop
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.post(
            "/api/kb/default/bibtex/async",
            json={"bibtex": "@article{x, doi={10.1/x}, title={T}}"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job_id" in body
        # Yield to event loop so create_task can run
        row = None
        for _ in range(40):
            await asyncio.sleep(0.05)
            resp = await ac.get(f"/api/jobs/{body['job_id']}")
            row = resp.json()
            if row.get("status") == "done":
                break
    assert row["status"] == "done"
    assert row["result"]["added_papers"] == 1
    assert captured_calls.get("called") is True


def test_async_bibtex_503_when_jobs_unconfigured(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "job_registry", None, raising=False)
    client = TestClient(app)
    r = client.post("/api/kb/default/bibtex/async", json={"bibtex": "@article{x}"})
    assert r.status_code == 503


# ---------------------------------------------------------------------------
# Async DOI endpoint tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_dois_returns_job_id_and_runs(monkeypatch):
    """The async DOI endpoint validates input, creates a job, and runs the worker."""
    import tempfile

    import httpx

    from perspicacite.jobs.registry import JobRegistry
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.web import state as state_mod
    from perspicacite.web.routers import kb as kb_router

    captured: dict = {}

    async def fake_worker(*, name, dois, job_id, registry, **kw):
        captured["called"] = True
        captured["name"] = name
        captured["dois"] = list(dois)
        await registry.publish(job_id, {"type": "progress", "done": 1, "doi": dois[0]})
        await registry.finish(job_id, {"added_papers": 1, "added_chunks": 2,
                                       "skipped_duplicates": 0, "failed": []})

    monkeypatch.setattr(kb_router, "_dois_ingest_worker", fake_worker)

    tmp = tempfile.mkdtemp()
    db_path = Path(tmp) / "p.db"
    ss = SessionStore(db_path)
    await ss.init_db()
    jr = JobRegistry(db_path=db_path)
    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)
    monkeypatch.setattr(state_mod.app_state, "job_registry", jr, raising=False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        r = await client.post("/api/kb/default/dois/async", json={"dois": ["10.1/x", "10.1/y"]})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "job_id" in body
        assert body.get("total") == 2
        # Poll until done
        row = None
        for _ in range(60):
            await asyncio.sleep(0.05)
            row = (await client.get(f"/api/jobs/{body['job_id']}")).json()
            if row.get("status") == "done":
                break
    assert row["status"] == "done"
    assert row["result"]["added_papers"] == 1
    assert captured.get("called") is True


def test_async_dois_503_when_jobs_unconfigured(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "job_registry", None, raising=False)
    client = TestClient(app)
    r = client.post("/api/kb/default/dois/async", json={"dois": ["10.1/x"]})
    assert r.status_code == 503


def test_background_tasks_set_exists():
    """kb.py must hold a strong reference to in-flight ingestion tasks."""
    from perspicacite.web.routers import kb as kb_router

    assert hasattr(kb_router, "_background_tasks"), \
        "kb.py must keep a strong-ref set for fire-and-forget tasks"
    assert isinstance(kb_router._background_tasks, set)


def test_async_dois_400_when_too_many(monkeypatch):
    """Reject >200 DOIs same as the sync endpoint."""
    import tempfile

    from perspicacite.jobs.registry import JobRegistry
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.web import state as state_mod

    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "p.db"
    ss = SessionStore(db)
    jr = JobRegistry(db_path=db)
    monkeypatch.setattr(state_mod.app_state, "job_registry", jr, raising=False)
    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)
    client = TestClient(app)
    r = client.post("/api/kb/default/dois/async", json={"dois": [f"10.1/{i}" for i in range(201)]})
    assert r.status_code == 400
