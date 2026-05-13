import json
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path):
    from perspicacite.config.schema import Config
    from perspicacite.web.routers import kb as kb_router

    cfg = Config()
    cfg.capsule.root = tmp_path / "capsules"
    cfg.capsule.root.mkdir(parents=True)

    class _State:
        config = cfg

    app = FastAPI()
    app.state.app_state = _State()
    app.include_router(kb_router.router)
    return TestClient(app), cfg.capsule.root


def _write_capsule(root: Path, paper_id: str) -> None:
    safe = paper_id.replace(":", "_").replace("/", "__")
    fig_dir = root / safe / "figures"
    fig_dir.mkdir(parents=True)
    fig_dir.joinpath("index.json").write_text(json.dumps([
        {"filename": "fig_p001_i00.png", "page": 1, "index": 0,
         "figure_number": "1", "caption": "C", "subcomponent_label": "",
         "panel_files": []}
    ]))
    fig_dir.joinpath("fig_p001_i00.png").write_bytes(b"\x89PNGfake")


def test_list_figures(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(f"/api/capsule/{quote('doi:10.1/x', safe='')}/figures")
    assert r.status_code == 200
    data = r.json()
    assert data[0]["filename"] == "fig_p001_i00.png"


def test_get_figure_bytes(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(f"/api/capsule/{quote('doi:10.1/x', safe='')}/figure/pdf_p1_i0")
    assert r.status_code == 200
    assert r.content == b"\x89PNGfake"
    assert r.headers["content-type"].startswith("image/")


def test_unknown_figure_404(client):
    tc, root = client
    _write_capsule(root, "doi:10.1/x")
    r = tc.get(f"/api/capsule/{quote('doi:10.1/x', safe='')}/figure/pdf_p99_i99")
    assert r.status_code == 404


def test_unknown_capsule_404(client):
    tc, _ = client
    r = tc.get(f"/api/capsule/{quote('doi:10.1/missing', safe='')}/figures")
    assert r.status_code == 404
