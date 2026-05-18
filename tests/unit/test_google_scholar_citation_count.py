"""Unit tests for Google Scholar citation-count extraction."""
import pytest

from perspicacite.search.google_scholar_playwright import _extract_citation_count


@pytest.mark.parametrize("text,expected", [
    ("Cited by 42 Related articles All 3 versions", 42),
    ("Cited by 0 Related articles", 0),
    ("Cited by 12345 ...", 12345),
])
def test_extract_basic(text, expected):
    assert _extract_citation_count(text) == expected


def test_extract_missing_returns_none():
    assert _extract_citation_count("Related articles only") is None


def test_extract_empty_returns_none():
    assert _extract_citation_count("") is None
    assert _extract_citation_count(None) is None
