"""Tests for provenance read endpoints (Task 1.9)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.collector import ProvenanceCollector
from perspicacite.provenance.store import ProvenanceStore


@pytest.mark.asyncio
async def test_get_message_provenance_endpoint(tmp_path: Path, monkeypatch) -> None:
    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")
    c = ProvenanceCollector(conversation_id="conv-1", message_id="msg-1",
                            rag_mode="basic", request_params={})
    c.add_retrieval(paper_id="p1", doi="10.1/a", title="A", score=0.5,
                    kb_name=None, content_type=None, pipeline_step=None,
                    rank=0, stage_label="basic.retrieve")
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


def test_provenance_endpoints_503_when_unconfigured(monkeypatch) -> None:
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app
    monkeypatch.setattr(state_mod.app_state, "provenance_store", None, raising=False)
    client = TestClient(app)
    r = client.get("/api/conversations/x/messages/y/provenance")
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_export_ro_crate_returns_zip(tmp_path, monkeypatch) -> None:
    import io
    import zipfile
    from perspicacite.memory.session_store import SessionStore
    from perspicacite.models.messages import Message
    from perspicacite.provenance.collector import ProvenanceCollector
    from perspicacite.provenance.store import ProvenanceStore
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app

    ss = SessionStore(tmp_path / "p.db")
    await ss.init_db()
    ps = ProvenanceStore(db_path=tmp_path / "p.db", sidecar_dir=tmp_path / "provenance")

    # Seed a conversation with one user + one assistant message
    conv = await ss.create_conversation(session_id="s1", kb_name="default", title="Q on X")
    await ss.add_message(conv.id, Message(role="user", content="What is X?"))
    await ss.add_message(conv.id, Message(role="assistant", content="X is foo.",
                                          sources=[{"doi": "10.1/a", "title": "Paper A", "year": 2024}]))

    # Seed a provenance record for the assistant message
    msgs = (await ss.get_conversation(conv.id)).messages
    asst = next(m for m in msgs if m.role == "assistant")
    c = ProvenanceCollector(conversation_id=conv.id, message_id=asst.id, rag_mode="basic", request_params={})
    c.add_llm_call(stage_label="basic.answer", provider="p", model="m",
                   prompt_messages=[{"role": "user", "content": "q"}], response_text="r",
                   prompt_tokens=1, completion_tokens=1, latency_ms=1.0)
    await ps.save(c.finalize())

    monkeypatch.setattr(state_mod.app_state, "session_store", ss, raising=False)
    monkeypatch.setattr(state_mod.app_state, "provenance_store", ps, raising=False)

    client = TestClient(app)
    r = client.get(f"/api/conversations/{conv.id}/export?format=ro-crate")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/zip")
    assert "attachment" in r.headers.get("content-disposition", "")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "ro-crate-metadata.json" in names
    assert "conversation.md" in names
    assert "sources.json" in names
    assert "provenance/llm-calls.jsonl" in names


def test_export_unknown_format_400() -> None:
    from perspicacite.web.app import app
    client = TestClient(app)
    r = client.get("/api/conversations/c/export?format=banana")
    assert r.status_code in (400, 503)  # 503 if session_store is None in this test app
