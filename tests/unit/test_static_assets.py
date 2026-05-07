"""Smoke tests for static asset mount and structure of templates/index.html."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from perspicacite.web import app

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"

CSS_FILES = ["theme", "base", "layout", "chat", "kb", "survey"]
JS_FILES = ["utils", "databases", "mode", "conversations", "chat"]


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


@pytest.mark.parametrize("name", CSS_FILES)
def test_css_link_present(index_html, name):
    assert f'href="/static/css/{name}.css"' in index_html, f"Missing href for {name}.css"
    assert 'rel="stylesheet"' in index_html


@pytest.mark.parametrize("name", CSS_FILES)
def test_css_file_served(client, name):
    response = client.get(f"/static/css/{name}.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_css_load_order(index_html):
    """All <link> tags must appear in the documented dependency order."""
    positions = {name: index_html.find(f"/static/css/{name}.css") for name in CSS_FILES}
    for name in CSS_FILES:
        assert positions[name] != -1, f"{name}.css not found in page"
    actual = sorted(positions, key=positions.get)
    assert actual == CSS_FILES, f"CSS load order is {actual}, expected {CSS_FILES}"


def test_no_inline_style(index_html):
    """No <style>...</style> blocks should remain after full extraction."""
    pattern = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL)
    matches = pattern.findall(index_html)
    assert not matches, f"Found {len(matches)} inline <style> blocks"


@pytest.mark.parametrize("name", JS_FILES)
def test_js_script_present(index_html, name):
    pattern = rf'<script\s+[^>]*src="/static/js/{name}\.js"'
    assert re.search(pattern, index_html), f"Missing <script> for {name}.js"


@pytest.mark.parametrize("name", JS_FILES)
def test_js_file_served(client, name):
    response = client.get(f"/static/js/{name}.js")
    assert response.status_code == 200
