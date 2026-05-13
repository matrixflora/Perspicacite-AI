"""Unit tests for GET /api/jobs/{id} and GET /api/jobs/{id}/events."""

from __future__ import annotations

from fastapi.testclient import TestClient
from perspicacite.web.app import app


def test_get_job_404_unknown():
    client = TestClient(app)
    r = client.get("/api/jobs/does-not-exist")
    assert r.status_code in (404, 503)


def test_get_job_events_404_unknown():
    client = TestClient(app)
    r = client.get("/api/jobs/does-not-exist/events")
    assert r.status_code in (404, 503)
