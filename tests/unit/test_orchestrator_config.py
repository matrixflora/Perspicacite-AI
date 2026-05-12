"""Tests for AgenticOrchestrator configurable parameters."""


def test_orchestrator_map_reduce_cap_default():
    """AgenticOrchestrator defaults map_reduce_max_papers to 8."""
    from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

    o = AgenticOrchestrator(
        llm_client=None,
        tool_registry=None,
        embedding_provider=None,
        vector_store=None,
    )
    assert o.map_reduce_max_papers == 8


def test_orchestrator_map_reduce_cap_param():
    """AgenticOrchestrator respects an explicit map_reduce_max_papers param."""
    from perspicacite.rag.agentic.orchestrator import AgenticOrchestrator

    o2 = AgenticOrchestrator(
        llm_client=None,
        tool_registry=None,
        embedding_provider=None,
        vector_store=None,
        map_reduce_max_papers=3,
    )
    assert o2.map_reduce_max_papers == 3
