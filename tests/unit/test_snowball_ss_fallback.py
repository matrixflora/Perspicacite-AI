"""Unit tests for the snowball → Semantic Scholar fallback path."""
from __future__ import annotations

import pytest

from perspicacite.pipeline.snowball import (
    _seed_needs_ss_fallback,
    _ss_id_for_seed,
)


def test_seed_needs_ss_fallback_arxiv_doi_uppercase():
    assert _seed_needs_ss_fallback("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_arxiv_doi_lowercase():
    assert _seed_needs_ss_fallback("10.48550/arxiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"}) is True


def test_seed_needs_ss_fallback_crossref_doi_returns_false():
    assert _seed_needs_ss_fallback("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"}) is False


def test_seed_needs_ss_fallback_work_without_doi_returns_true():
    # OpenAlex resolved via title.search but has no canonical DOI
    assert _seed_needs_ss_fallback("foo", {"id": "W123", "doi": None}) is True
    assert _seed_needs_ss_fallback("foo", {"id": "W123"}) is True


def test_seed_needs_ss_fallback_none_work_returns_false():
    # If the seed didn't resolve at all, snowball already skipped it — the
    # SS branch never runs. Returning False here is defensive.
    assert _seed_needs_ss_fallback("10.48550/arxiv.X", None) is False


def test_ss_id_for_seed_arxiv_doi():
    """When the seed DOI is an arxiv DOI, prefer the ArXiv: form so
    Semantic Scholar can resolve the preprint directly."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401", {"doi": "10.48550/arxiv.2005.11401"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_arxiv_doi_with_version_suffix():
    """arXiv ids can carry a vN version suffix; SS accepts the base id."""
    out = _ss_id_for_seed("10.48550/arXiv.2005.11401v2", {"doi": "10.48550/arxiv.2005.11401v2"})
    assert out == "ArXiv:2005.11401"


def test_ss_id_for_seed_crossref_doi_falls_back_to_doi_prefix():
    out = _ss_id_for_seed("10.1145/3404835.3462913", {"doi": "10.1145/3404835.3462913"})
    assert out == "DOI:10.1145/3404835.3462913"
