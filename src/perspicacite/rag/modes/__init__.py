"""RAG mode implementations for benchmark comparison.

Modes:
- BasicRAGMode: Simple retrieval + generation (single query)
- AdvancedRAGMode: Query rephrasing + WRRF scoring + optional refinement
- DeepResearchRAGMode: Multi-cycle research with planning (canonical name, formerly ProfoundRAGMode)
- ProfoundRAGMode: Backward-compat alias for DeepResearchRAGMode
- AgenticRAGMode: Intent-based agentic RAG with tool use
- LiteratureSurveyRAGMode: Systematic field mapping with theme identification
- ContradictionRAGMode: Agreement / disagreement / open-question detection
- ReasoningRAGMode: Claim-graph-grounded reasoning with provenance/contradiction strategies
"""

from perspicacite.rag.modes.advanced import AdvancedRAGMode
from perspicacite.rag.modes.agentic import AgenticRAGMode
from perspicacite.rag.modes.basic import BasicRAGMode
from perspicacite.rag.modes.contradiction import ContradictionRAGMode
from perspicacite.rag.modes.deep_research import DeepResearchRAGMode, ProfoundRAGMode
from perspicacite.rag.modes.literature_survey import LiteratureSurveyRAGMode
from perspicacite.rag.modes.reasoning import ReasoningRAGMode

__all__ = [
    "AdvancedRAGMode",
    "AgenticRAGMode",
    "BasicRAGMode",
    "ContradictionRAGMode",
    "DeepResearchRAGMode",
    "LiteratureSurveyRAGMode",
    "ProfoundRAGMode",
    "ReasoningRAGMode",
]
