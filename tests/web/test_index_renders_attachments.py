"""Smoke test: index.html ships the markup hooks the JS expects.

Asserts that the template has the panel container divs and a Prism
CDN link (with `integrity=` attribute present). Doesn't validate the
SRI hash itself — that's a CDN-side concern.
"""
from pathlib import Path


def _index_html():
    return Path("templates/index.html").read_text("utf-8")


def test_index_has_code_excerpts_panel_container():
    html = _index_html()
    assert 'id="code-excerpts-panel"' in html


def test_index_has_figures_panel_container():
    html = _index_html()
    assert 'id="figures-panel"' in html


def test_index_loads_prism_from_cdn_with_sri():
    html = _index_html()
    assert "prismjs" in html.lower()
    assert "integrity=" in html
