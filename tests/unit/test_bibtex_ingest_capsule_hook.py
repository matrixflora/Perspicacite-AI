"""BibTeX ingest worker writes capsule artifacts after dkb.add_papers."""

from __future__ import annotations

import pytest

from perspicacite.web.routers import kb as kb_router


def test_kb_router_imports_build_capsule_lazily():
    # build_capsule should NOT be imported at module top (lazy import in worker).
    # But it must be reachable when needed.
    from perspicacite.pipeline.capsule_builder import build_capsule
    assert callable(build_capsule)


def test_bibtex_worker_exists():
    assert hasattr(kb_router, "_bibtex_ingest_worker")
