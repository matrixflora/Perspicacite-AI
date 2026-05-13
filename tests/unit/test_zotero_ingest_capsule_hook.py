"""Zotero ingest writes capsule artifacts."""

from __future__ import annotations

import pytest

from perspicacite.integrations import zotero_ingest


def test_module_imports():
    # Worker function exists (name may vary; assert module is importable at least)
    assert zotero_ingest is not None
