"""Document chunk models."""

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from perspicacite.models.papers import PaperSource


class ChunkMetadata(BaseModel):
    """Metadata for a document chunk."""

    model_config = {"frozen": True}

    paper_id: str
    chunk_index: int
    section: Optional[str] = None
    page_number: Optional[int] = None
    source: PaperSource = PaperSource.BIBTEX
    title: Optional[str] = None
    authors: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    url: Optional[str] = None
    # Local-doc / smart-chunking extensions (all optional):
    content_type: Optional[str] = None  # "pdf" | "markdown" | "code" | "text"
    language: Optional[str] = None  # python | typescript | ...
    heading_path: Optional[list[str]] = None  # markdown heading stack
    source_file_path: Optional[str] = None  # absolute path for local files
    # ASB-aligned provenance (Cycle A 2026-05-13) — all optional, additive.
    source_section: Optional[str] = None
    page: Optional[int] = None
    char_span: Optional[tuple[int, int]] = None
    figure_refs: list[str] = Field(default_factory=list)
    table_refs: list[str] = Field(default_factory=list)
    resource_refs: list[str] = Field(default_factory=list)
    parent_paper_id: Optional[str] = None
    is_external: bool = False
    # Sub-project A (code-aware chunking) extensions — all optional.
    symbol_name: Optional[str] = None
    symbol_kind: Optional[str] = None  # "function" | "class" | "method" | "cell" | "module"
    parent_class: Optional[str] = Field(
        None,
        description="If symbol_kind is a method, the enclosing class name. None otherwise.",
    )
    start_line: Optional[int] = None   # 1-indexed inclusive
    end_line: Optional[int] = None     # 1-indexed inclusive
    docstring: Optional[str] = None    # ≤500 chars, truncated
    imports: list[str] = Field(default_factory=list)

    # Sub-project B (per-type embedding routing) — records which embedder
    # actually produced the chunk's vector. None when not yet embedded.
    embedding_model: Optional[str] = None

    # Cite-graph enrichment fields (2026-05-15 spec).
    source_via: Optional[Literal["bundle", "enrichment", "cite_graph", "cite_graph_script"]] = None
    cited_tool: Optional[str] = None
    discovery_score: Optional[float] = None

    def __repr__(self) -> str:
        return (
            f"ChunkMetadata(paper_id='{self.paper_id}', "
            f"chunk_index={self.chunk_index}, section='{self.section}')"
        )


class DocumentChunk(BaseModel):
    """A chunk of a document with metadata."""

    model_config = {"frozen": False}

    id: str
    text: str
    metadata: ChunkMetadata
    embedding: Optional[list[float]] = None

    def __repr__(self) -> str:
        text_preview = self.text[:50].replace("\n", " ")
        return f"DocumentChunk(id='{self.id}', text='{text_preview}...')"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for serialization."""
        return {
            "id": self.id,
            "text": self.text,
            "metadata": self.metadata.model_dump(),
            "embedding": self.embedding,
        }
