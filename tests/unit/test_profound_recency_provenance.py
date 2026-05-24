"""Smoke tests: profound.py wires recency weighting + provenance events.

These are source-level checks — no live services needed.
"""
from __future__ import annotations


def test_profound_imports_recency_helper():
    from perspicacite.rag.modes import deep_research as m

    with open(m.__file__) as fh:
        src = fh.read()
    assert "apply_recency_weighting_to_papers" in src or "apply_recency_weighting" in src


def test_profound_imports_get_collector():
    from perspicacite.rag.modes import deep_research as m

    with open(m.__file__) as fh:
        src = fh.read()
    assert "get_collector" in src
    assert "add_trace" in src


def test_profound_has_stage_label_on_llm():
    from perspicacite.rag.modes import deep_research as m

    with open(m.__file__) as fh:
        src = fh.read()
    assert 'stage="profound.' in src or "stage='profound." in src
