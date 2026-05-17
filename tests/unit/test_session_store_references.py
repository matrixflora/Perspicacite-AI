"""Tests for SessionStore kb_paper_references table and methods."""
from pathlib import Path

import pytest

from perspicacite.memory.session_store import SessionStore


@pytest.fixture
async def store(tmp_path: Path) -> SessionStore:
    s = SessionStore(tmp_path / "test.db")
    await s.init_db()
    return s


async def test_store_paper_reference_returns_true_for_new(store):
    result = await store.store_paper_reference(
        kb_name="kb1",
        doi="10.1/test",
        title="Test Paper",
        authors=["Alice", "Bob"],
        year=2023,
        abstract="Some abstract",
        survey_query="microbiome",
    )
    assert result is True


async def test_store_paper_reference_returns_false_for_duplicate(store):
    await store.store_paper_reference(
        kb_name="kb1", doi="10.1/test", title="Test Paper",
        authors=["Alice"], year=2023, abstract=None, survey_query=None,
    )
    result = await store.store_paper_reference(
        kb_name="kb1", doi="10.1/test", title="Test Paper",
        authors=["Alice"], year=2023, abstract=None, survey_query=None,
    )
    assert result is False


async def test_store_same_doi_different_kb_both_succeed(store):
    r1 = await store.store_paper_reference(
        kb_name="kb1", doi="10.1/test", title="Test Paper",
        authors=[], year=None, abstract=None, survey_query=None,
    )
    r2 = await store.store_paper_reference(
        kb_name="kb2", doi="10.1/test", title="Test Paper",
        authors=[], year=None, abstract=None, survey_query=None,
    )
    assert r1 is True
    assert r2 is True


async def test_get_paper_references_returns_stored(store):
    await store.store_paper_reference(
        kb_name="kb1", doi="10.1/a", title="Paper A",
        authors=["Alice"], year=2022, abstract="abstract A", survey_query="query1",
    )
    refs = await store.get_paper_references("kb1")
    assert len(refs) == 1
    r = refs[0]
    assert r["doi"] == "10.1/a"
    assert r["title"] == "Paper A"
    assert r["authors"] == ["Alice"]
    assert r["year"] == 2022
    assert r["abstract"] == "abstract A"
    assert r["survey_query"] == "query1"


async def test_get_paper_references_filters_by_kb(store):
    await store.store_paper_reference(
        kb_name="kb1", doi="10.1/x", title="X",
        authors=[], year=None, abstract=None, survey_query=None,
    )
    await store.store_paper_reference(
        kb_name="kb2", doi="10.1/y", title="Y",
        authors=[], year=None, abstract=None, survey_query=None,
    )
    refs_kb1 = await store.get_paper_references("kb1")
    refs_kb2 = await store.get_paper_references("kb2")
    assert len(refs_kb1) == 1
    assert refs_kb1[0]["doi"] == "10.1/x"
    assert len(refs_kb2) == 1
    assert refs_kb2[0]["doi"] == "10.1/y"
