"""ASB-aligned IMRaD section splitter — vendored (adapted to plain text input)."""

from __future__ import annotations

import pytest


def test_detects_imrad():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = (
        "## Abstract\nWe present X.\n\n"
        "## Introduction\nBackground stuff.\n\n"
        "## Methods\nWe did Y.\n\n"
        "## Results\nWe found Z.\n\n"
        "## Discussion\nThis implies …\n"
    )
    sm = split_sections(txt)
    assert sm.sections_detected is True
    assert set(sm.sections) >= {"abstract", "intro", "methods", "results", "discussion"}
    assert "We did Y." in sm.sections["methods"]


def test_fallback_full_text():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = "Just one big blob of prose without any IMRaD headings whatsoever."
    sm = split_sections(txt)
    assert sm.sections_detected is False
    assert sm.sections == {"full_text": txt}


def test_alias_mapping():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    txt = (
        "## Background\nbg\n\n## Materials and Methods\nmm\n\n"
        "## Results and Discussion\nrd\n\n## Supporting Information\nsi\n"
    )
    sm = split_sections(txt)
    assert "bg" in sm.sections["intro"]
    assert "mm" in sm.sections["methods"]
    assert "rd" in sm.sections["results"]
    assert "si" in sm.sections["supplementary"]


def test_empty_input():
    from perspicacite.pipeline.parsers.section_splitter import split_sections
    sm = split_sections("")
    assert sm.sections_detected is False
    assert sm.sections == {"full_text": ""}
