"""Unit tests for the Zotero web router endpoints.

Tests that /api/zotero/status and /api/zotero/push respond correctly
when Zotero is not configured, without making real network calls.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from perspicacite.web.app import app


def _make_fake_cfg(enabled: bool = False):
    return type("c", (), {
        "zotero": type("z", (), {
            "enabled": enabled,
            "api_key": "key" if enabled else "",
            "library_id": "lib" if enabled else "",
            "library_type": "user",
            "collection_key": "",
        })(),
    })()


def test_zotero_status_endpoint(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "config", _make_fake_cfg(enabled=False), raising=False)
    client = TestClient(app)
    r = client.get("/api/zotero/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_zotero_status_endpoint_enabled(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "config", _make_fake_cfg(enabled=True), raising=False)
    client = TestClient(app)
    r = client.get("/api/zotero/status")
    assert r.status_code == 200
    assert r.json()["enabled"] is True


def test_zotero_push_endpoint_503_when_unconfigured(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "config", _make_fake_cfg(enabled=False), raising=False)
    client = TestClient(app)
    r = client.post("/api/zotero/push", json={"dois": ["10.1/x"]})
    assert r.status_code == 503


def test_zotero_push_endpoint_400_too_many_dois(monkeypatch):
    from perspicacite.web import state as state_mod

    monkeypatch.setattr(state_mod.app_state, "config", _make_fake_cfg(enabled=True), raising=False)
    client = TestClient(app)
    r = client.post("/api/zotero/push", json={"dois": [f"10.1/x{i}" for i in range(101)]})
    assert r.status_code == 400
