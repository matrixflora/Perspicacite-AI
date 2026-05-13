"""Smoke tests for static asset mount and structure of templates/index.html."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from perspicacite.web import app

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "static"

CSS_FILES = ["theme", "base", "layout", "chat", "kb", "survey"]
JS_FILES = [
    "utils",
    "databases",
    "mode",
    "conversations",
    "chat",
    "kb",
    "kb_stats",
    "paper_detail",
    "survey",
    "provenance",
    "main",
]


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


def test_js_load_order(index_html):
    """All <script> tags must appear in the documented dependency order."""
    positions = {}
    for name in JS_FILES:
        idx = index_html.find(f"/static/js/{name}.js")
        assert idx != -1, f"{name}.js not found in page"
        positions[name] = idx
    actual = sorted(positions, key=positions.get)
    assert actual == JS_FILES, f"JS load order is {actual}, expected {JS_FILES}"


def test_no_inline_script(index_html):
    """No <script> tags without a src= attribute (i.e. no inline JS)."""
    pattern = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>.*?</script>", re.DOTALL)
    matches = pattern.findall(index_html)
    assert not matches, f"Found {len(matches)} inline <script> blocks"


def test_phase5_static_assets(index_html):
    """Phase-5 new JS files exist on disk and are referenced from index.html."""
    assert (REPO_ROOT / "static" / "js" / "kb_stats.js").exists(), "kb_stats.js missing from disk"
    assert "kb_stats.js" in index_html, "kb_stats.js not referenced in index.html"

    assert (REPO_ROOT / "static" / "js" / "paper_detail.js").exists(), (
        "paper_detail.js missing from disk"
    )
    assert "paper_detail.js" in index_html, "paper_detail.js not referenced in index.html"

    assert "contradiction" in (REPO_ROOT / "static" / "js" / "mode.js").read_text(), (
        "mode.js does not contain 'contradiction'"
    )


def test_phase5_html_elements(index_html):
    """Phase-5 required DOM elements are present in index.html."""
    assert 'id="paper-detail-panel"' in index_html, "#paper-detail-panel div missing"
    assert 'id="kb-stats-container"' in index_html, "#kb-stats-container div missing"
    assert 'id="conv-search-input"' in index_html, "#conv-search-input missing"
    assert 'id="advanced-options-details"' in index_html, "#advanced-options-details missing"
    assert 'value="contradiction"' in index_html, "contradiction option missing from mode dropdown"


def test_provenance_js_present():
    """provenance.js must exist on disk and be referenced from index.html."""
    root = Path(__file__).resolve().parents[2]
    assert (root / "static/js/provenance.js").exists(), "provenance.js missing from disk"
    html = (root / "templates/index.html").read_text()
    assert "provenance.js" in html, "provenance.js not referenced in index.html"


def test_provenance_disclosure_css():
    """chat.css must contain the .provenance-disclosure rule."""
    root = Path(__file__).resolve().parents[2]
    css = (root / "static/css/chat.css").read_text()
    assert ".provenance-disclosure" in css, ".provenance-disclosure missing from chat.css"
