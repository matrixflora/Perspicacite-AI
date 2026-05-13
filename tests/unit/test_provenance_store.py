from pathlib import Path

import pytest

from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_provenance_table_created_idempotently(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "p.db")
    await store.init_db()
    await store.init_db()  # idempotency


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
    assert rec["llm_calls"] == []  # sidecar arrives in Task 2.1


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
