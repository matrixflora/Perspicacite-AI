"""Smoke tests for static asset mount and structure of templates/index.html."""

import re
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


@pytest.fixture
def index_html(client):
    response = client.get("/")
    assert response.status_code == 200
    return response.text


@pytest.mark.parametrize("name", ["theme", "base", "layout", "kb"])
def test_css_link_present(index_html, name):
    assert f'href="/static/css/{name}.css"' in index_html, f"Missing href for {name}.css"
    assert 'rel="stylesheet"' in index_html


@pytest.mark.parametrize("name", ["theme", "base", "layout", "kb"])
def test_css_file_served(client, name):
    response = client.get(f"/static/css/{name}.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
