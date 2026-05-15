from __future__ import annotations

from perspicacite.models.papers import PaperSource


def test_enum_has_legacy_values():
    """Legacy values must keep working — no regressions."""
    assert PaperSource.BIBTEX.value == "bibtex"
    assert PaperSource.SCILEX.value == "scilex"
    assert PaperSource.WEB_SEARCH.value == "web_search"
    assert PaperSource.USER_UPLOAD.value == "user_upload"
    assert PaperSource.CITATION_FOLLOW.value == "citation_follow"
    assert PaperSource.LOCAL.value == "local"


def test_enum_has_new_database_values():
    """Audit 2026-05-15 finding #5: explicit DB sources required."""
    assert PaperSource.OPENALEX.value == "openalex"
    assert PaperSource.PUBMED.value == "pubmed"
    assert PaperSource.ARXIV.value == "arxiv"
    assert PaperSource.CROSSREF.value == "crossref"


def test_enum_constructs_from_string_for_chroma_roundtrip():
    """retrieval/chroma_store.py:599 calls PaperSource(metadata.get('source','bibtex'))."""
    assert PaperSource("openalex") is PaperSource.OPENALEX
    assert PaperSource("pubmed") is PaperSource.PUBMED
    assert PaperSource("arxiv") is PaperSource.ARXIV
    assert PaperSource("crossref") is PaperSource.CROSSREF
