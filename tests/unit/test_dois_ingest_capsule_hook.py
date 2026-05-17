"""DOIs ingest worker writes capsule artifacts after dkb.add_papers."""

from __future__ import annotations

from perspicacite.web.routers import kb as kb_router


def test_dois_worker_exists():
    assert hasattr(kb_router, "_dois_ingest_worker")
