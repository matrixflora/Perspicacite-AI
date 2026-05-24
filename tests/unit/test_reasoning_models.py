"""Unit tests for RAGMode.REASONING + RAGRequest.reasoning_strategy."""

import pytest
from pydantic import ValidationError

from perspicacite.models.rag import RAGMode, RAGRequest


def test_reasoning_mode_enum_value():
    assert RAGMode.REASONING.value == "reasoning"


def test_reasoning_strategy_accepts_valid_values():
    for strat in ("provenance", "contradiction", "graph", "evidence_graded"):
        req = RAGRequest(query="q", mode=RAGMode.REASONING, reasoning_strategy=strat)
        assert req.reasoning_strategy == strat


def test_reasoning_strategy_defaults_to_none():
    req = RAGRequest(query="q", mode=RAGMode.REASONING)
    assert req.reasoning_strategy is None


def test_reasoning_strategy_rejects_invalid():
    with pytest.raises(ValidationError):
        RAGRequest(query="q", mode=RAGMode.REASONING, reasoning_strategy="garbage")
