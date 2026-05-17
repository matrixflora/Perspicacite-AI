"""local-files and local-paths router endpoints."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from perspicacite.web.app import app as fastapi_app


def _state(allowed_roots: list[Path] | None = None):
    return SimpleNamespace(
        config=SimpleNamespace(
            local_docs=SimpleNamespace(allowed_roots=allowed_roots or []),
            knowledge_base=SimpleNamespace(
                chunk_size=1000, chunk_overlap=200,
                markdown_heading_aware=True, code_language_aware=True,
            ),
        ),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
        session_store=SimpleNamespace(get_kb_metadata=AsyncMock(return_value=SimpleNamespace(
            collection_name="perspicacite_local", paper_count=0, chunk_count=0,
        ))),
        vector_store=None,
        embedding_provider=None,
        pdf_parser=None,
    )


def _patch_state(monkeypatch, state):
    # The kb router does `from perspicacite.web.state import app_state`, which
    # creates a local binding. Patch both the source module and the router's
    # local binding so all code paths see the fake state.
    monkeypatch.setattr("perspicacite.web.state.app_state", state)
    monkeypatch.setattr("perspicacite.web.routers.kb.app_state", state)


def test_local_paths_returns_503_when_allowed_roots_empty(monkeypatch):
    _patch_state(monkeypatch, _state(allowed_roots=[]))
    client = TestClient(fastapi_app)
    r = client.post("/api/kb/local/local-paths", json={"paths": ["/etc/hosts"]})
    assert r.status_code == 503


def test_local_files_accepts_upload(monkeypatch):
    # Stub out the background ingestion worker so the test doesn't try to run
    # the real pipeline against our fake app_state.
    async def _fake_ingest(**kwargs):
        return None

    monkeypatch.setattr(
        "perspicacite.web.routers.kb.ingest_local_documents", _fake_ingest
    )
    _patch_state(monkeypatch, _state())
    client = TestClient(fastapi_app)
    files = {"files": ("notes.md", BytesIO(b"# Hi\n\nBody"), "text/markdown")}
    r = client.post("/api/kb/local/local-files", files=files)
    assert r.status_code in (200, 202)
    assert "job_id" in r.json()


def test_local_paths_accepts_valid_path(tmp_path, monkeypatch):
    async def _fake_ingest(**kwargs):
        return None

    monkeypatch.setattr(
        "perspicacite.web.routers.kb.ingest_local_documents", _fake_ingest
    )
    f = tmp_path / "n.md"
    f.write_text("# t\n\nbody")
    _patch_state(monkeypatch, _state(allowed_roots=[tmp_path]))
    client = TestClient(fastapi_app)
    r = client.post("/api/kb/local/local-paths", json={"paths": [str(f)], "recursive": False})
    assert r.status_code in (200, 202)
