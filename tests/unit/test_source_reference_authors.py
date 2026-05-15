from __future__ import annotations

import pytest

from perspicacite.models.rag import SourceReference


def test_authors_accepts_list_of_strings():
    s = SourceReference(title="T", authors=["Alice", "Bob"])
    assert s.authors == ["Alice", "Bob"]


def test_authors_defaults_to_empty_list():
    s = SourceReference(title="T")
    assert s.authors == []


def test_authors_coerces_comma_joined_string():
    # Backward-compat: pre-fix call sites passed "A, B, C"
    s = SourceReference(title="T", authors="Alice, Bob, Carol")
    assert s.authors == ["Alice", "Bob", "Carol"]


def test_authors_coerces_and_separated_string():
    s = SourceReference(title="T", authors="Alice and Bob")
    assert s.authors == ["Alice", "Bob"]


def test_authors_coerces_none_to_empty():
    s = SourceReference(title="T", authors=None)
    assert s.authors == []


def test_authors_single_string_becomes_single_element_list():
    s = SourceReference(title="T", authors="OnlyAuthor")
    assert s.authors == ["OnlyAuthor"]


def test_to_citation_uses_first_author_with_et_al():
    s = SourceReference(title="T", authors=["Jumper", "Evans", "Pritzel"], year=2021)
    assert s.to_citation() == "[Jumper et al., 2021]"


def test_to_citation_single_author_no_et_al():
    s = SourceReference(title="T", authors=["Solo"], year=2024)
    assert s.to_citation() == "[Solo, 2024]"


def test_to_citation_empty_authors_unknown():
    s = SourceReference(title="T", year=2024)
    assert s.to_citation() == "[Unknown, 2024]"
