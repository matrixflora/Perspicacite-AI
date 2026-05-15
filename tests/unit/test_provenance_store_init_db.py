from __future__ import annotations

import pytest

from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_init_db_creates_provenance_table_in_fresh_db(tmp_path):
    db = tmp_path / "audit.sqlite"
    sidecar = tmp_path / "sidecar"
    store = ProvenanceStore(db_path=db, sidecar_dir=sidecar)

    # Round-trip a record against a *fresh* DB (no SessionStore involved).
    await store.init_db()
    await store.save({
        "message_id": "m1",
        "conversation_id": "c1",
        "rag_mode": "basic",
        "request_params": {"q": "x"},
        "retrieval_events": [],
        "mode_trace": [],
        "llm_calls": [],
    })

    rec = await store.get_for_message("m1")
    assert rec is not None
    assert rec["message_id"] == "m1"
    assert rec["rag_mode"] == "basic"


@pytest.mark.asyncio
async def test_save_escalates_when_schema_missing(tmp_path):
    """save() must NOT silently swallow OperationalError when the table is
    absent — that masked the original ProvenanceStore-standalone bug."""
    import aiosqlite

    db = tmp_path / "no_schema.sqlite"
    sidecar = tmp_path / "sidecar"
    # Touch the DB so the file exists but has no `provenance` table.
    async with aiosqlite.connect(db) as raw:
        await raw.execute("CREATE TABLE other (x INTEGER)")
        await raw.commit()

    store = ProvenanceStore(db_path=db, sidecar_dir=sidecar)
    # No init_db called → save() must raise, not log-and-return.
    with pytest.raises(aiosqlite.OperationalError):
        await store.save({
            "message_id": "m2",
            "conversation_id": None,
            "rag_mode": "basic",
            "llm_calls": [],
        })


@pytest.mark.asyncio
async def test_session_store_still_creates_provenance_table(tmp_path):
    """Regression: SessionStore must continue to create the provenance
    table for the existing shared-DB path."""
    import aiosqlite

    from perspicacite.memory.session_store import SessionStore

    db = tmp_path / "shared.sqlite"
    ss = SessionStore(db_path=db)
    await ss.init_db()

    async with aiosqlite.connect(db) as raw:
        cur = await raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='provenance'"
        )
        row = await cur.fetchone()
    assert row is not None, "SessionStore must still create the provenance table"
