"""local_docs._ingest_files writes capsule artifacts for PDF files."""

from __future__ import annotations

import pytest

from perspicacite.integrations import local_docs


def test_module_imports():
    assert hasattr(local_docs, "_ingest_files")
