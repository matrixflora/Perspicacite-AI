"""Tests for SearchFilters.source_skill and its translation to a Chroma where clause."""
from __future__ import annotations

from perspicacite.models.search import SearchFilters
from perspicacite.retrieval.chroma_store import _filters_to_where


def test_search_filters_source_skill_default_none():
    f = SearchFilters()
    assert f.source_skill is None


def test_filters_to_where_includes_source_skill_when_set():
    where = _filters_to_where(SearchFilters(source_skill="scrna-qc"))
    assert where is not None
    # Single-condition where clauses are returned bare (not wrapped in $and).
    # Either it's the bare dict, or one of the $and conditions.
    if "$and" in where:
        assert {"source_skill": "scrna-qc"} in where["$and"]
    else:
        assert where == {"source_skill": "scrna-qc"}


def test_filters_to_where_combines_source_skill_with_other_filters():
    where = _filters_to_where(
        SearchFilters(year_min=2020, source_skill="scrna-qc")
    )
    assert where is not None
    assert "$and" in where
    assert {"source_skill": "scrna-qc"} in where["$and"]
    assert {"year": {"$gte": 2020}} in where["$and"]
