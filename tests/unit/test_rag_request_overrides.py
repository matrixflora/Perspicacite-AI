"""Unit tests for RAGRequest per-call budget/parallelism override fields."""
import pytest
from pydantic import ValidationError

from perspicacite.models.rag import RAGRequest


def test_max_total_seconds_valid():
    r = RAGRequest(query="q", max_total_seconds=120)
    assert r.max_total_seconds == 120


def test_max_total_seconds_below_floor_rejected():
    with pytest.raises(ValidationError):
        RAGRequest(query="q", max_total_seconds=10)


def test_max_total_seconds_above_ceiling_rejected():
    with pytest.raises(ValidationError):
        RAGRequest(query="q", max_total_seconds=10000)


def test_batch_size_bounds():
    RAGRequest(query="q", batch_size=1)   # ok — floor
    RAGRequest(query="q", batch_size=100)  # ok — ceiling
    with pytest.raises(ValidationError):
        RAGRequest(query="q", batch_size=0)
    with pytest.raises(ValidationError):
        RAGRequest(query="q", batch_size=999)


def test_crossref_concurrency_bounds():
    RAGRequest(query="q", crossref_concurrency=5)
    with pytest.raises(ValidationError):
        RAGRequest(query="q", crossref_concurrency=100)


def test_defaults_are_none():
    r = RAGRequest(query="q")
    assert r.max_total_seconds is None
    assert r.batch_size is None
    assert r.crossref_concurrency is None
