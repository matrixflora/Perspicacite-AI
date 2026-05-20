"""Tests for the deterministic database advisor heuristic."""
from __future__ import annotations

from perspicacite.search.database_advisor import (
    DatabaseSuggestion,
    suggest_databases_for_query,
)
from perspicacite.search.scilex_adapter import KNOWN_DATABASES


def test_biomedical_query_recommends_pubmed():
    result = suggest_databases_for_query("CRISPR gene editing in human cells")
    assert isinstance(result, DatabaseSuggestion)
    assert "pubmed" in result.databases


def test_cs_ml_query_recommends_arxiv():
    result = suggest_databases_for_query(
        "transformer neural network architecture for language models"
    )
    assert "arxiv" in result.databases


def test_chemistry_query_recommends_pubchem():
    result = suggest_databases_for_query(
        "synthesis of novel organic compound molecule reaction"
    )
    assert "pubchem" in result.databases


def test_high_energy_physics_recommends_inspire():
    result = suggest_databases_for_query(
        "quark gluon plasma high-energy particle collider physics"
    )
    assert "inspire" in result.databases


def test_unknown_query_returns_broad_default_superset():
    result = suggest_databases_for_query("the weather was nice yesterday")
    assert {"semantic_scholar", "openalex", "crossref"}.issubset(set(result.databases))


def test_all_recommended_are_known_databases():
    for query in [
        "CRISPR gene therapy",
        "deep learning model",
        "chemistry molecule synthesis",
        "particle physics collider",
        "random unrelated text",
    ]:
        result = suggest_databases_for_query(query)
        assert result.databases, "must always recommend something"
        assert all(db in KNOWN_DATABASES for db in result.databases)


def test_databases_are_deduped_preserving_order():
    result = suggest_databases_for_query("biomedical machine learning genomics")
    assert len(result.databases) == len(set(result.databases))


def test_reasoning_is_non_empty():
    result = suggest_databases_for_query("anything")
    assert isinstance(result.reasoning, str)
    assert result.reasoning


def test_hints_can_steer_recommendation():
    result = suggest_databases_for_query("general query", hints=["chemistry"])
    assert "pubchem" in result.databases
