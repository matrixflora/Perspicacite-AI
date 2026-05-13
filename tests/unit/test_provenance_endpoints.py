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
