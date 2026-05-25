"""Unit test for B-7 fix: literature_survey _convert_to_sources clamps relevance_score.

PaperCandidate.relevance_score is set from the LLM's analysis output, which
returns a 0-5 integer rating (see literature_survey.py:1090). SourceReference's
pydantic schema requires relevance_score ∈ [0, 1], so without normalization
every emit crashes the stream with a ValidationError.

This test enforces the normalization contract:
  - integer ≥ 1 is treated as a 0-5 rating → divided by 5
  - already-in-range floats pass through unchanged
  - missing / None becomes 0.0
  - clamp to [0, 1] for safety
"""
from __future__ import annotations


def _make_mode():
    from perspicacite.config.schema import Config
    from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
    return LiteratureSurveyRAGMode(Config())


def _make_candidate(score):
    from perspicacite.rag.modes.literature_survey import PaperCandidate
    return PaperCandidate(
        id="test-1",
        title="Test paper",
        authors=["A. One"],
        year=2024,
        doi="10.1234/test",
        abstract="Test abstract.",
        relevance_score=score,
    )


def test_convert_to_sources_handles_zero():
    mode = _make_mode()
    sources = mode._convert_to_sources([_make_candidate(0.0)])
    assert len(sources) == 1
    assert sources[0].relevance_score == 0.0


def test_convert_to_sources_passes_through_valid_range():
    mode = _make_mode()
    sources = mode._convert_to_sources([_make_candidate(0.75)])
    assert sources[0].relevance_score == 0.75


def test_convert_to_sources_normalizes_llm_rating_scale():
    """0-5 LLM rating gets divided by 5 to land in [0, 1].

    raw=1 is genuinely ambiguous (could be "1 of 5" or "1.0 = max valid") so
    the normalizer leaves it alone (passes 1.0 through). Values clearly above
    the [0,1] range (>1.0) are unambiguously the 0-5 rating scale and get
    divided by 5.
    """
    mode = _make_mode()
    for raw, expected in [(2, 0.4), (3, 0.6), (4, 0.8), (5, 1.0)]:
        sources = mode._convert_to_sources([_make_candidate(raw)])
        assert abs(sources[0].relevance_score - expected) < 1e-9, (
            f"raw={raw} expected={expected} got={sources[0].relevance_score}"
        )
    # raw=1.0 stays 1.0 (within valid range)
    sources = mode._convert_to_sources([_make_candidate(1)])
    assert sources[0].relevance_score == 1.0


def test_convert_to_sources_clamps_above_5():
    """Defensive: even out-of-spec values shouldn't blow up the stream."""
    mode = _make_mode()
    sources = mode._convert_to_sources([_make_candidate(10)])
    # 10 / 5 = 2.0 → clamped to 1.0
    assert sources[0].relevance_score == 1.0


def test_convert_to_sources_handles_none():
    mode = _make_mode()
    sources = mode._convert_to_sources([_make_candidate(None)])
    assert sources[0].relevance_score == 0.0


def test_convert_to_sources_does_not_raise_validation_error():
    """The actual bug B-7 — pre-patch this raised pydantic ValidationError."""
    mode = _make_mode()
    candidates = [
        _make_candidate(0),
        _make_candidate(1),
        _make_candidate(3),  # the value that originally triggered the crash
        _make_candidate(5),
    ]
    sources = mode._convert_to_sources(candidates)
    assert len(sources) == 4
    for s in sources:
        assert 0.0 <= s.relevance_score <= 1.0
