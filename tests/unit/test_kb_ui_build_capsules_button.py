"""KB panel has a Build-capsules button wired to the async endpoint."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_has_build_capsules_button():
    html = (ROOT / "templates" / "index.html").read_text()
    assert 'data-testid="kb-build-capsules"' in html


def test_kb_js_posts_to_build_capsules():
    js = (ROOT / "static" / "js" / "kb.js").read_text()
    assert "/build-capsules" in js
