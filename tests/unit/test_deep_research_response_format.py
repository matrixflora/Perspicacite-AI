# tests/unit/test_deep_research_response_format.py
"""B-8 regression: ProfoundRAGMode JSON-returning LLM calls must request
response_format={"type": "json_object"} so the LLM emits syntactically valid JSON.

Without this flag, deepseek/openrouter sometimes produces markdown code-fences
or truncated JSON, causing parse failures in ~67% of easy claims.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from perspicacite.rag.modes.deep_research import PlanStep, ProfoundRAGMode, ResearchStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_llm(answer: str) -> MagicMock:
    """Build a stub LLM whose complete() captures kwargs and returns *answer*."""
    llm = MagicMock()
    captured: list[dict] = []

    async def _complete(**kwargs):
        captured.append(kwargs)
        return answer

    llm.complete = _complete
    llm._captured = captured
    return llm


def _make_mode() -> ProfoundRAGMode:
    """Build a ProfoundRAGMode with stub config (no real services)."""
    mode = ProfoundRAGMode.__new__(ProfoundRAGMode)
    mode.config = MagicMock()
    mode._iteration_summaries = []
    mode._model_name = None
    return mode


# ---------------------------------------------------------------------------
# _is_question_answered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_question_answered_passes_response_format():
    """`_is_question_answered` must pass response_format=json_object to the LLM."""
    mode = _make_mode()
    llm = _mock_llm(json.dumps({"question_answered": True, "confidence": 0.9}))

    step = ResearchStep(
        step_purpose="find mechanism",
        query="protein folding mechanism",
        success=True,
        analysis="Found relevant papers.",
        key_findings=["Finding A"],
    )

    answered, conf = await mode._is_question_answered(
        cycle_steps=[step],
        original_query="How does protein folding work?",
        llm=llm,
    )

    assert answered is True
    assert conf == pytest.approx(0.9)

    # The LLM must have received response_format={"type": "json_object"}
    assert llm._captured, "LLM.complete() was never called"
    call_kwargs = llm._captured[0]
    assert call_kwargs.get("response_format") == {"type": "json_object"}, (
        "B-8: _is_question_answered must request response_format=json_object "
        f"but got response_format={call_kwargs.get('response_format')!r}"
    )


@pytest.mark.asyncio
async def test_is_question_answered_empty_steps_returns_false():
    """Early-out: empty cycle_steps must return (False, 0.0) without LLM call."""
    mode = _make_mode()
    llm = _mock_llm("{}")

    answered, conf = await mode._is_question_answered(
        cycle_steps=[],
        original_query="anything",
        llm=llm,
    )

    assert answered is False
    assert conf == pytest.approx(0.0)
    assert llm._captured == [], "LLM should not be called on empty cycle_steps"


# ---------------------------------------------------------------------------
# _create_plan
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_plan_passes_response_format():
    """`_create_plan` must pass response_format=json_object to the LLM."""
    mode = _make_mode()
    plan_json = json.dumps({
        "plan": [
            {"step": 1, "purpose": "find papers", "search_query": "test query"},
        ]
    })
    llm = _mock_llm(plan_json)

    steps = await mode._create_plan(query="What causes neurodegeneration?", llm=llm)

    # The LLM must have received response_format={"type": "json_object"}
    assert llm._captured, "LLM.complete() was never called"
    call_kwargs = llm._captured[0]
    assert call_kwargs.get("response_format") == {"type": "json_object"}, (
        "B-8: _create_plan must request response_format=json_object "
        f"but got response_format={call_kwargs.get('response_format')!r}"
    )
    # Plan must be non-empty (parse succeeded)
    assert len(steps) >= 1


@pytest.mark.asyncio
async def test_create_plan_falls_back_on_empty_response():
    """If LLM returns empty string, _create_plan falls back to default plan."""
    mode = _make_mode()
    llm = _mock_llm("")  # empty response triggers the warning + fallback

    steps = await mode._create_plan(query="test", llm=llm)

    # Fallback plan has exactly 2 steps
    assert len(steps) == 2


# ---------------------------------------------------------------------------
# _create_iteration_summary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_iteration_summary_passes_response_format():
    """`_create_iteration_summary` (reflection/continuation decision) must
    request response_format=json_object so the LLM returns parseable JSON.
    """
    mode = _make_mode()
    mode.use_relevancy_optimization = False  # use ORIGINAL prompt branch
    summary_json = json.dumps({
        "findings": "Protein folding involves chaperones.",
        "missing": ["kinetic details"],
        "should_continue": True,
        "reasoning": "Need more data on kinetics.",
    })
    llm = _mock_llm(summary_json)

    result = await mode._create_iteration_summary(
        question="How does protein folding work?",
        steps=[
            ResearchStep(
                step_purpose="initial search",
                query="protein folding mechanism",
                success=True,
                analysis="Found chaperone papers.",
                key_findings=["Chaperones assist folding"],
            )
        ],
        llm=llm,
    )

    assert llm._captured, "LLM.complete() was never called"
    call_kwargs = llm._captured[0]
    assert call_kwargs.get("response_format") == {"type": "json_object"}, (
        "B-8: _create_iteration_summary must request response_format=json_object "
        f"but got response_format={call_kwargs.get('response_format')!r}"
    )
    assert result.get("should_continue") is True
    assert result.get("findings")


# ---------------------------------------------------------------------------
# _assess_documents_quality
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assess_documents_quality_passes_response_format():
    """`_assess_documents_quality` must request response_format=json_object."""
    mode = _make_mode()
    assessment_json = json.dumps({
        "is_sufficient": False,
        "missing_aspects": ["pharmacokinetics", "clinical trials"],
    })
    llm = _mock_llm(assessment_json)

    # Build minimal document-like objects that the method can handle
    fake_doc = {"full_text": "This paper discusses drug mechanisms...", "title": "Test paper"}

    sufficient, missing = await mode._assess_documents_quality(
        query="What is the drug's mechanism of action?",
        documents=[fake_doc],
        llm=llm,
        step_purpose="find mechanism papers",
    )

    assert llm._captured, "LLM.complete() was never called"
    call_kwargs = llm._captured[0]
    assert call_kwargs.get("response_format") == {"type": "json_object"}, (
        "B-8: _assess_documents_quality must request response_format=json_object "
        f"but got response_format={call_kwargs.get('response_format')!r}"
    )
    assert sufficient is False
    assert "pharmacokinetics" in missing


# ---------------------------------------------------------------------------
# _review_and_adjust_plan (two sequential LLM calls)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_and_adjust_plan_both_calls_pass_response_format():
    """`_review_and_adjust_plan` makes two sequential LLM calls (EVALUATE + ADJUST).
    Both must pass response_format=json_object (B-8).

    We trigger the ADJUST call by making the first (EVALUATE) call return
    recommendation="modify_plan", which forces the second call.
    """
    mode = _make_mode()

    eval_response = json.dumps({
        "recommendation": "modify_plan",
        "question_type": "answerable",
        "evaluation": "Needs more retrieval steps.",
        "reasoning": "Current plan insufficient.",
    })
    adjust_response = json.dumps({
        "plan": ["revised step"],
        "queries": ["revised query"],
        "reasoning": "Added retrieval step.",
        "strategy_change": "Expanded plan.",
    })

    # Multi-call mock: first call returns eval_response, second returns adjust_response
    call_index = [0]
    captured: list[dict] = []

    async def _multi_complete(**kwargs):
        captured.append(kwargs)
        idx = call_index[0]
        call_index[0] += 1
        return eval_response if idx == 0 else adjust_response

    llm = MagicMock()
    llm.complete = _multi_complete
    llm._captured = captured

    request = MagicMock()
    request.query = "How does protein aggregation cause neurodegeneration?"
    request.model = None
    request.provider = None

    working_plan = [
        PlanStep(step_number=1, purpose="find papers", query="protein aggregation"),
        PlanStep(step_number=2, purpose="verify mechanism", query="aggregation neurotoxicity"),
    ]
    completed_step = ResearchStep(
        step_purpose="find papers",
        query="protein aggregation",
        success=True,
        analysis="Found 5 papers.",
        key_findings=["Aggregates form plaques"],
    )

    result = await mode._review_and_adjust_plan(
        request=request,
        working_plan=working_plan,
        completed_steps=[completed_step],
        current_step_index=0,
        llm=llm,
    )

    assert len(captured) == 2, f"Expected 2 LLM calls (EVALUATE + ADJUST), got {len(captured)}"

    # Both calls must request response_format=json_object
    for i, call_kwargs in enumerate(captured):
        assert call_kwargs.get("response_format") == {"type": "json_object"}, (
            f"B-8: LLM call #{i + 1} in _review_and_adjust_plan must request "
            f"response_format=json_object but got {call_kwargs.get('response_format')!r}"
        )

    # Verify the result parsed correctly from adjust_response
    assert "plan" in result or "reasoning" in result


@pytest.mark.asyncio
async def test_review_and_adjust_plan_evaluate_only_when_continue_plan():
    """`_review_and_adjust_plan` must NOT make the second (ADJUST) call
    when the first call's recommendation is 'continue_plan'.  Still must
    pass response_format to the first call.
    """
    mode = _make_mode()

    eval_response = json.dumps({
        "recommendation": "continue_plan",
        "reasoning": "Plan is still appropriate.",
        "question_type": "answerable",
    })

    captured: list[dict] = []

    async def _single_complete(**kwargs):
        captured.append(kwargs)
        return eval_response

    llm = MagicMock()
    llm.complete = _single_complete

    request = MagicMock()
    request.query = "What is the mechanism of action?"
    request.model = None
    request.provider = None

    working_plan = [
        PlanStep(step_number=1, purpose="initial search", query="mechanism of action"),
    ]
    completed_step = ResearchStep(
        step_purpose="initial search",
        query="mechanism of action",
        success=True,
        analysis="Found relevant papers.",
        key_findings=["Mechanism involves X"],
    )

    await mode._review_and_adjust_plan(
        request=request,
        working_plan=working_plan,
        completed_steps=[completed_step],
        current_step_index=0,
        llm=llm,
    )

    # Only ONE call should have been made (EVALUATE only)
    assert len(captured) == 1, f"Expected 1 LLM call (continue_plan path), got {len(captured)}"
    assert captured[0].get("response_format") == {"type": "json_object"}


# ---------------------------------------------------------------------------
# _generate_contextual_queries
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_generate_contextual_queries_passes_response_format():
    """`_generate_contextual_queries` must pass response_format=json_object (B-8)."""
    mode = _make_mode()
    # Stub attributes used by the method
    mode.wrrf_rephrases = 2

    queries_json = json.dumps({"queries": ["query A", "query B"]})
    llm = _mock_llm(queries_json)

    # Build a minimal initial_document (dict form)
    initial_documents = [{"full_text": "Document about protein folding.", "title": "Paper 1"}]

    result = await mode._generate_contextual_queries(
        original_query="How does protein folding work?",
        initial_documents=initial_documents,
        missing_aspects=["kinetic details", "chaperone involvement"],
        llm=llm,
    )

    assert llm._captured, "LLM.complete() was never called"
    # The method first calls _summarize_snippet (non-JSON, no response_format) once
    # per initial_document, then makes the GENERATE_CONTEXTUAL_QUERIES_PROMPT call
    # WITH response_format.  We must have at least 2 calls and the last one must
    # carry response_format=json_object.
    assert len(llm._captured) >= 2, (
        f"Expected ≥2 LLM calls (summarize + generate), got {len(llm._captured)}"
    )
    # Find the call that has response_format — it must exist and must equal json_object
    rf_calls = [kw for kw in llm._captured if kw.get("response_format") is not None]
    assert rf_calls, (
        "B-8: no LLM call in _generate_contextual_queries had response_format set; "
        f"all calls: {[kw.get('response_format') for kw in llm._captured]!r}"
    )
    assert rf_calls[0].get("response_format") == {"type": "json_object"}, (
        "B-8: _generate_contextual_queries must request response_format=json_object "
        f"but got response_format={rf_calls[0].get('response_format')!r}"
    )
    assert isinstance(result, list)
    assert len(result) <= 4
