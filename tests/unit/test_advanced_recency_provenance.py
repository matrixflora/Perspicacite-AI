"""Smoke tests: advanced.py wires recency weighting + provenance events.

These are source-level checks — no live services needed.
"""
from __future__ import annotations


def test_advanced_calls_recency_helper_when_recency_weight_set():
    """Smoke test: advanced.py imports apply_recency_weighting_to_papers."""
    from perspicacite.rag.modes import advanced as adv_mod

    # The module should reference the paper-dict variant somewhere
    with open(adv_mod.__file__) as fh:
        src = fh.read()
    assert "apply_recency_weighting_to_papers" in src


def test_advanced_passes_stage_label_to_llm():
    """advanced.py should pass a stage='advanced.*' kwarg on at least one llm call."""
    from perspicacite.rag.modes import advanced as adv_mod

    with open(adv_mod.__file__) as fh:
        src = fh.read()
    # Accept both kwarg-style (stage="advanced.") and dict-style ("stage": "advanced.")
    assert (
        'stage="advanced.' in src
        or "stage='advanced." in src
        or '"stage": "advanced.' in src
        or "'stage': 'advanced." in src
    )


def test_advanced_pushes_provenance():
    """advanced.py should call get_collector and add_retrieval at least once."""
    from perspicacite.rag.modes import advanced as adv_mod

    with open(adv_mod.__file__) as fh:
        src = fh.read()
    assert "get_collector" in src
    assert "add_retrieval" in src
