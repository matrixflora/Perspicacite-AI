"""
True Agentic RAG with LLM-driven orchestration.

This module implements a ReAct-style agent that:
1. Analyzes user intent
2. Plans steps dynamically
3. Executes tools iteratively
4. Reflects on results
5. Maintains conversation context
"""

from .intent import Intent, IntentClassifier, IntentResult
from .llm_adapter import LLMAdapter
from .orchestrator import AgenticOrchestrator, AgentSession
from .planner import Plan, ResearchPlanner, Step, StepType

__all__ = [
    "AgentSession",
    "AgenticOrchestrator",
    "Intent",
    "IntentClassifier",
    "IntentResult",
    "LLMAdapter",
    "Plan",
    "ResearchPlanner",
    "Step",
    "StepType",
]
