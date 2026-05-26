"""Endpoints for similarity expansion: /score, /cutoff, /commit."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient


def _state(*, has_registry=True, kb_exists=True):
    return SimpleNamespace(
        job_registry=(
            SimpleNamespace(
                create=AsyncMock(return_value="J1"),
                finish=AsyncMock(),
                fail=AsyncMock(),
            )
            if has_registry
            else None
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


# ---- cutoff ----


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


# ---- score ----


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
        return SimpleNamespace(
            candidates=[], histogram=[], samples=[], seed_count=0, method="hybrid"
        )

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


# ---- commit ----


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
