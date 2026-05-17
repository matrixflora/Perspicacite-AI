"""POST /api/kb/{name}/build-capsules surface."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from perspicacite.web.app import app as fastapi_app


def _state(tmp_root, n: int = 2):
    rows = [
        {"paper_id": f"doi:10.1/{i}", "title": str(i), "doi": f"10.1/{i}", "year": 2024, "authors": ""}
        for i in range(n)
    ]
    return SimpleNamespace(
        config=SimpleNamespace(capsule=SimpleNamespace(
            enabled=True, auto_build_on_ingest=True,
            root=tmp_root, min_version="0.1",
        )),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
        vector_store=SimpleNamespace(list_paper_metadata=AsyncMock(return_value=rows)),
        session_store=SimpleNamespace(
            get_kb_metadata=AsyncMock(return_value=SimpleNamespace(collection_name="c")),
        ),
    )


def test_build_capsules_async_returns_job(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "perspicacite.pipeline.capsule_builder.build_capsule",
        AsyncMock(return_value={"status": "built", "figures": 0, "chunks": 0, "blocks": 0, "resources": 0}),
    )
    state = _state(tmp_path)
    monkeypatch.setattr("perspicacite.web.state.app_state", state)
    monkeypatch.setattr("perspicacite.web.routers.kb.app_state", state)
    client = TestClient(fastapi_app)
    r = client.post("/api/kb/k1/build-capsules")
    assert r.status_code in (200, 202)
    body = r.json()
    assert "job_id" in body
    assert "sse_url" in body
