"""Smoke test: Zotero ingest UI assets are present."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_html_has_zotero_button():
    html = (ROOT / "templates/index.html").read_text()
    assert 'data-testid="build-kbs-from-zotero"' in html


def test_kb_js_references_zotero_plan_endpoint():
    js = (ROOT / "static/js/kb.js").read_text()
    assert "/api/zotero/plan" in js
    assert "/api/zotero/build-kbs/async" in js


def test_index_html_has_zotero_modal():
    html = (ROOT / "templates/index.html").read_text()
    assert "zotero-build-modal" in html
