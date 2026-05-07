"""Smoke tests for static asset mount and structure of templates/index.html."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from perspicacite.web import app

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"


@pytest.fixture
def client():
    return TestClient(app)


def test_static_dir_exists():
    assert STATIC_DIR.exists(), f"{STATIC_DIR} does not exist"
    assert (STATIC_DIR / "css").is_dir()
    assert (STATIC_DIR / "js").is_dir()


def test_static_mount_serves_files(client):
    response = client.get("/static/css/.gitkeep")
    assert response.status_code == 200
