from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_index_html_has_dropzone():
    html = (ROOT / "templates/index.html").read_text()
    assert "data-testid=\"kb-local-dropzone\"" in html


def test_kb_js_handles_local_files_post():
    js = (ROOT / "static/js/kb.js").read_text()
    assert "/api/kb/" in js and "/local-files" in js
