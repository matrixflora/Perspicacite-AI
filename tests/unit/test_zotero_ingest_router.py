"""Zotero ingest router: /plan + /build-kbs/async."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


def _fake_state(zotero_enabled: bool):
    return SimpleNamespace(
        config=SimpleNamespace(
            zotero=SimpleNamespace(
                enabled=zotero_enabled,
                api_key="k" if zotero_enabled else "",
                library_id="42" if zotero_enabled else "",
                library_type="user",
                collection_key="",
            ),
        ),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
    )


def test_plan_returns_503_when_disabled(monkeypatch):
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app as fastapi_app
    from perspicacite.web.routers import zotero_ingest as zi

    monkeypatch.setattr(state_mod, "app_state", _fake_state(False))
    monkeypatch.setattr(zi, "app_state", _fake_state(False))
    client = TestClient(fastapi_app)
    r = client.get("/api/zotero/plan")
    assert r.status_code == 503


def test_plan_returns_plan_when_enabled(monkeypatch):
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app as fastapi_app
    from perspicacite.web.routers import zotero_ingest as zi

    state = _fake_state(True)
    monkeypatch.setattr(state_mod, "app_state", state)
    monkeypatch.setattr(zi, "app_state", state)

    async def _plan(*a, **k):
        from perspicacite.integrations.zotero_ingest import ZoteroKBPlanEntry
        return [ZoteroKBPlanEntry(
            kb_name="K1",
            source_collection_key=None,
            source_collection_name=None,
            item_count=1, with_doi_count=1, with_pdf_count=0, with_notes_count=0,
        )]

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _plan)
    client = TestClient(fastapi_app)
    r = client.get("/api/zotero/plan")
    assert r.status_code == 200
    body = r.json()
    assert "plan" in body
    assert body["plan"][0]["kb_name"] == "K1"


def test_build_kbs_async_returns_job_id(monkeypatch):
    from perspicacite.web import state as state_mod
    from perspicacite.web.app import app as fastapi_app
    from perspicacite.web.routers import zotero_ingest as zi

    state = _fake_state(True)
    monkeypatch.setattr(state_mod, "app_state", state)
    monkeypatch.setattr(zi, "app_state", state)

    async def _build(*a, **k):
        return {"per_kb": []}

    monkeypatch.setattr(zi, "build_kbs_from_zotero", _build)
    client = TestClient(fastapi_app)
    body = {"plan": [{
        "kb_name": "TestKB",
        "source_collection_key": "C1",
        "source_collection_name": "Coll1",
        "item_count": 1, "with_doi_count": 1,
        "with_pdf_count": 0, "with_notes_count": 0,
    }]}
    r = client.post("/api/zotero/build-kbs/async", json=body)
    assert r.status_code in (200, 202)
    j = r.json()
    assert "job_id" in j
    assert "sse_url" in j
