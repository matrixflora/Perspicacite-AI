"""RAG models."""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from perspicacite.models.search import SearchFilters


class RAGMode(str, Enum):
    """RAG modes for benchmark comparison.

    BASIC: Simple retrieval + generation (single query, no refinement)
    ADVANCED: Query rephrasing + hybrid retrieval + WRRF scoring + optional refinement
    PROFOUND: Multi-cycle research with planning, web search, reflection (from v1)
    AGENTIC: Intent-based agentic RAG with tool use
    LITERATURE_SURVEY: Systematic field mapping with theme identification and structured output
    CONTRADICTION: Identify agreement / disagreement / open questions across papers
    """

    BASIC = "basic"
    ADVANCED = "advanced"
    PROFOUND = "profound"
    AGENTIC = "agentic"
    LITERATURE_SURVEY = "literature_survey"
    CONTRADICTION = "contradiction"


class SourceReference(BaseModel):
    """Reference to a source paper."""

    title: str
    authors: list[str] = Field(default_factory=list)
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    chunk_text: Optional[str] = None
    kb_name: Optional[str] = None

    @field_validator("authors", mode="before")
    @classmethod
    def _coerce_authors(cls, v):
        """Accept str (legacy), None, or list — always store list[str].

        Audit 2026-05-15 finding #2: the field was previously
        ``Optional[str]`` which broke construction from upstream
        ``normalize_paper_dict`` (returns ``list[str]``). This validator
        keeps backward compat for the comma-joined-string call sites
        that pre-dated the fix.
        """
        if v is None:
            return []
        if isinstance(v, list):
            return [str(a).strip() for a in v if str(a).strip()]
        if isinstance(v, str):
            # Split on " and " (BibTeX-style) then commas.
            parts: list[str] = []
            for chunk in v.replace(" and ", ",").split(","):
                chunk = chunk.strip()
                if chunk:
                    parts.append(chunk)
            return parts
        return [str(v)]

    def __repr__(self) -> str:
        return f"SourceReference(title='{self.title[:40]}...', score={self.relevance_score:.2f})"

    def to_citation(self, style: str = "nature") -> str:
        """Format as citation string."""
        if not self.authors:
            author_part = "Unknown"
        elif len(self.authors) == 1:
            author_part = self.authors[0]
        else:
            author_part = f"{self.authors[0]} et al."
        year_part = f", {self.year}" if self.year else ""
        return f"[{author_part}{year_part}]"


class RAGRequest(BaseModel):
    """Request for RAG query."""

    query: str
    kb_name: str = "default"
    mode: RAGMode = RAGMode.BASIC
    provider: str = "deepseek"
    model: str = "deepseek-chat"
    max_iterations: Optional[int] = None
    use_web_search: bool = False
    filters: Optional[SearchFilters] = None
    conversation_id: Optional[str] = None
    refined_query: Optional[str] = None
    kb_scope: Optional[str] = None
    # v1: optional evaluator LLM (same client; different model/provider per call)
    evaluator_provider: Optional[str] = None
    evaluator_model: Optional[str] = None
    databases: List[str] = Field(
        default_factory=lambda: ["semantic_scholar", "openalex", "pubmed"],
        description="List of databases to search",
    )
    conversation_history: Optional[List[Dict[str, str]]] = Field(
        default=None,
        description="Recent chat turns (role/content) for query rewrite and generation context",
    )
    max_papers_retrieval: Optional[int] = Field(
        default=None,
        ge=1,
        le=10,
        description="Hard cap on papers loaded in two-pass; None uses mode default",
    )
    bm25_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    vector_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    recency_weight: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    recency_half_life_years: Optional[float] = Field(default=None, gt=0.0)
    kb_names: Optional[List[str]] = None

    def __repr__(self) -> str:
        return (
            f"RAGRequest(query='{self.query[:50]}...', "
            f"mode='{self.mode.value}', kb='{self.kb_name}')"
        )


class FigureRef(BaseModel):
    """A figure attached to a RAG response for display in the GUI / MCP."""
    id: str
    paper_id: str
    label: Optional[str] = None      # e.g. "Figure 3"
    caption: Optional[str] = None
    source_url: Optional[str] = None  # paper DOI / page URL
    page: Optional[int] = None
    thumbnail_b64: Optional[str] = None  # small base64 PNG for inline display


class CodeExcerpt(BaseModel):
    """A code-chunk excerpt attached to a RAG response (sub-project C)."""
    id: str                            # e.g. "github:owner/repo@SHA:path#Lstart-Lend"
    paper_id: str
    file_path: str
    symbol_name: Optional[str] = None  # None for module chunks
    symbol_kind: str                   # "function" | "class" | "method" | "cell" | "module"
    language: str                      # "python" | "r" | etc.
    start_line: int
    end_line: int
    text: str
    source_url: str                    # e.g. GitHub blob URL with #L<s>-L<e>


class RAGResponse(BaseModel):
    """Response from RAG query."""

    answer: str
    sources: list[SourceReference] = Field(default_factory=list)
    mode: RAGMode
    iterations: int = 1
    confidence: Optional[float] = None
    research_plan: Optional[list[str]] = None
    web_search_used: bool = False
    tokens_used: Optional[int] = None
    figures: list[FigureRef] = Field(default_factory=list)
    code_excerpts: list[CodeExcerpt] = Field(default_factory=list)

    def __repr__(self) -> str:
        return (
            f"RAGResponse(mode='{self.mode.value}', "
            f"sources={len(self.sources)}, iterations={self.iterations})"
        )


class StreamEvent(BaseModel):
    """Structured SSE event for streaming responses."""

    event: Literal[
        "status",  # Processing status updates
        "content",  # Answer text delta
        "source",  # Source reference
        "reasoning",  # Chain-of-thought (Deep/Citation modes)
        "plan",  # Research plan step
        "tool_call",  # Tool invocation
        "tool_result",  # Tool result
        "error",  # Error message
        "done",  # Stream complete
        "code_excerpt",  # Code-chunk attachment (sub-project C, 2026-05-15)
        "figure_ref",  # Figure-reference attachment (sub-project C, 2026-05-15)
    ]
    data: str  # JSON-encoded payload

    def __repr__(self) -> str:
        return f"StreamEvent(event='{self.event}', data='{self.data[:50]}...')"

    @classmethod
    def status(cls, message: str) -> "StreamEvent":
        """Create a status event."""
        import json

        return cls(event="status", data=json.dumps({"message": message}))

    @classmethod
    def content(cls, delta: str) -> "StreamEvent":
        """Create a content delta event."""
        import json

        return cls(event="content", data=json.dumps({"delta": delta}))

    @classmethod
    def source(cls, source: "SourceReference") -> "StreamEvent":
        """Create a source event."""
        import json

        return cls(event="source", data=json.dumps(source.model_dump()))

    @classmethod
    def done(
        cls,
        conversation_id: str,
        tokens_used: int,
        mode: str,
        iterations: int,
    ) -> "StreamEvent":
        """Create a done event."""
        import json

        return cls(
            event="done",
            data=json.dumps(
                {
                    "conversation_id": conversation_id,
                    "tokens_used": tokens_used,
                    "mode": mode,
                    "iterations": iterations,
                }
            ),
        )

    @classmethod
    def code_excerpt(cls, payload: dict) -> "StreamEvent":
        """Create a code-excerpt event (sub-project C)."""
        import json
        return cls(event="code_excerpt", data=json.dumps(payload))

    @classmethod
    def figure_ref(cls, payload: dict) -> "StreamEvent":
        """Create a figure-ref event (sub-project C)."""
        import json
        return cls(event="figure_ref", data=json.dumps(payload))
