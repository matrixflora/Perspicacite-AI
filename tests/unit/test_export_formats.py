"""Tests for CSL JSON + RIS exporters (Wave 4.5)."""
import json

import pytest

from perspicacite.pipeline.export_kb import (
    render_csl_json_entry,
    render_ris_entry,
)


_PAPER = {
    "title": "Cool Paper About Quasars",
    "authors": ["Smith, J.", "Doe, A."],
    "year": 2024,
    "journal": "ApJ",
    "doi": "10.1234/cool",
    "abstract": "We show that quasars are interesting.",
}


def test_csl_json_basic_fields():
    item = render_csl_json_entry(_PAPER)
    assert item["type"] == "article-journal"
    assert item["title"] == "Cool Paper About Quasars"
    assert item["container-title"] == "ApJ"
    assert item["issued"] == {"date-parts": [[2024]]}
    assert item["DOI"] == "10.1234/cool"
    assert item["URL"] == "https://doi.org/10.1234/cool"
    assert item["abstract"] == "We show that quasars are interesting."
    # ID must be a non-empty string
    assert isinstance(item["id"], str) and item["id"]


def test_csl_json_multi_author_split():
    item = render_csl_json_entry(_PAPER)
    assert item["author"] == [
        {"family": "Smith", "given": "J."},
        {"family": "Doe", "given": "A."},
    ]


def test_csl_json_handles_string_authors():
    """`authors` may come in as a comma-and-and-separated string."""
    paper = {**_PAPER, "authors": "Smith, J. and Doe, A. and Roe, P."}
    item = render_csl_json_entry(paper)
    assert len(item["author"]) == 3
    assert item["author"][2] == {"family": "Roe", "given": "P."}


def test_csl_json_omits_missing_fields():
    paper = {"title": "Only Title"}
    item = render_csl_json_entry(paper)
    assert item["title"] == "Only Title"
    assert "DOI" not in item
    assert "URL" not in item
    assert "abstract" not in item


def test_csl_json_no_author_field():
    paper = {"title": "Anonymous"}
    item = render_csl_json_entry(paper)
    assert "author" not in item


def test_ris_basic_fields():
    out = render_ris_entry(_PAPER)
    lines = out.splitlines()
    assert lines[0] == "TY  - JOUR"
    assert "T1  - Cool Paper About Quasars" in lines
    assert "PY  - 2024" in lines
    assert "JO  - ApJ" in lines
    assert "DO  - 10.1234/cool" in lines
    assert "UR  - https://doi.org/10.1234/cool" in lines
    assert lines[-1] == "ER  - "


def test_ris_multi_author_repeats_AU():
    out = render_ris_entry(_PAPER)
    au_lines = [l for l in out.splitlines() if l.startswith("AU  - ")]
    assert au_lines == ["AU  - Smith, J.", "AU  - Doe, A."]


def test_ris_handles_string_authors():
    paper = {**_PAPER, "authors": "Smith, J. and Doe, A."}
    out = render_ris_entry(paper)
    au_lines = [l for l in out.splitlines() if l.startswith("AU  - ")]
    assert au_lines == ["AU  - Smith, J.", "AU  - Doe, A."]


def test_ris_escapes_newlines_in_fields():
    """Newlines inside a field would corrupt the line-oriented format —
    replace them with spaces."""
    paper = {
        **_PAPER,
        "title": "Line one\nLine two",
        "abstract": "Para one.\n\nPara two.",
    }
    out = render_ris_entry(paper)
    title_lines = [l for l in out.splitlines() if l.startswith("T1  - ")]
    assert len(title_lines) == 1
    assert "\n" not in title_lines[0]


def test_ris_omits_missing_fields():
    paper = {"title": "Only Title"}
    out = render_ris_entry(paper)
    assert "DO  - " not in out
    assert "AB  - " not in out
