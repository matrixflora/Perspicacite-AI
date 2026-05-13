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
    await asyncio.sleep(0)

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


@pytest.mark.asyncio
async def test_jobs_table_idempotent(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    await ss.init_db()  # idempotent


@pytest.mark.asyncio
async def test_jobs_get_unknown_returns_none(tmp_path: Path) -> None:
    ss = SessionStore(tmp_path / "p.db"); await ss.init_db()
    reg = JobRegistry(db_path=tmp_path / "p.db")
    assert await reg.get("nope") is None
