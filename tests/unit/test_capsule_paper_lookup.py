"""resolve_paper_from_metadata + locate_cached_pdf helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from perspicacite.pipeline.capsule_builder import (
    resolve_paper_from_metadata,
    locate_cached_pdf,
)


def test_resolve_paper_from_metadata():
    row = {
        "paper_id": "doi:10.1234/abc",
        "title": "Some title",
        "year": 2024,
        "doi": "10.1234/abc",
        "authors": "Doe, Jane; Smith, John",
    }
    p = resolve_paper_from_metadata(row)
    assert p.id == "doi:10.1234/abc"
    assert p.title == "Some title"
    assert p.year == 2024
    assert p.doi == "10.1234/abc"


def test_locate_cached_pdf_doi(tmp_path):
    pdfs = tmp_path / "data" / "papers"
    pdfs.mkdir(parents=True)
    target = pdfs / "10.1234_abc.pdf"
    target.write_bytes(b"%PDF-1.4")
    found = locate_cached_pdf({"paper_id": "doi:10.1234/abc", "doi": "10.1234/abc"}, root=pdfs)
    assert found == target


def test_locate_cached_pdf_missing(tmp_path):
    pdfs = tmp_path / "data" / "papers"
    pdfs.mkdir(parents=True)
    assert locate_cached_pdf({"paper_id": "doi:10.1234/xyz", "doi": "10.1234/xyz"}, root=pdfs) is None
