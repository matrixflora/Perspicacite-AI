"""Tests for walk_filtered and papers_from_directory."""
from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003

import pytest

from perspicacite.pipeline.github.bundle import BundleManifest, ContentConfig
from perspicacite.pipeline.github.chunk_producer import papers_from_directory
from perspicacite.pipeline.github.walk import walk_filtered


@pytest.fixture
def sample_bundle(tmp_path: Path) -> Path:
    """Create a minimal bundle directory tree for tests."""
    (tmp_path / "README.md").write_text("# Test bundle\nSee 10.1234/foo for details.")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "qc.py").write_text(
        '"""Quality control module.\n\nDoes QC stuff.\n"""\n\n'
        "def run_qc(samples):\n    \"\"\"Run the QC pipeline.\"\"\"\n    pass\n"
    )
    nb_content = json.dumps({
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},  # noqa: E501
        "cells": [
            {"cell_type": "markdown", "source": "## Analysis", "metadata": {}, "id": "1"},
            {"cell_type": "code", "source": "x = 1 + 1", "metadata": {}, "outputs": [], "execution_count": None, "id": "2"},  # noqa: E501
        ],
    })
    (tmp_path / "notebooks").mkdir()
    (tmp_path / "notebooks" / "qc.ipynb").write_text(nb_content)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "qc.cpython-312.pyc").write_bytes(b"FAKE")
    return tmp_path


def test_walker_respects_include_exclude(sample_bundle):
    files = walk_filtered(
        sample_bundle,
        include=["**/*.py", "**/*.md"],
        exclude=["__pycache__/**"],
    )
    paths = [f.as_posix() for f in files]
    assert "src/qc.py" in paths
    assert "README.md" in paths
    assert not any("__pycache__" in p for p in paths)


def test_chunk_producer_emits_markdown_paper(sample_bundle):
    manifest = BundleManifest(name="test", content=ContentConfig())
    papers = papers_from_directory(sample_bundle, manifest, "abc12345")
    md_papers = [p for p in papers if "README.md" in (p.metadata or {}).get("file_path", "")]
    assert md_papers, "Expected a README.md paper"
    assert md_papers[0].content_type == "docs"


def test_chunk_producer_handles_notebook(sample_bundle):
    manifest = BundleManifest(name="test", content=ContentConfig())
    papers = papers_from_directory(sample_bundle, manifest, "abc12345")
    nb_papers = [p for p in papers if ".ipynb" in (p.metadata or {}).get("file_path", "")]
    assert nb_papers, "Expected a notebook paper"
    assert nb_papers[0].content_type == "code"
    assert "Analysis" in (nb_papers[0].full_text or "")


def test_chunk_producer_extracts_docstrings(sample_bundle):
    manifest = BundleManifest(name="test", content=ContentConfig())
    papers = papers_from_directory(sample_bundle, manifest, "abc12345")
    py_papers = [p for p in papers if ".py" in (p.metadata or {}).get("file_path", "")]
    assert py_papers
    text = py_papers[0].full_text or ""
    assert "Quality control" in text or "run_qc" in text


def test_links_in_readme_attached_to_paper_metadata(sample_bundle):
    manifest = BundleManifest(name="test", content=ContentConfig())
    papers = papers_from_directory(sample_bundle, manifest, "abc12345")
    md_papers = [p for p in papers if "README.md" in (p.metadata or {}).get("file_path", "")]
    assert md_papers
    mined = (md_papers[0].metadata or {}).get("mined_dois", [])
    assert "10.1234/foo" in mined
