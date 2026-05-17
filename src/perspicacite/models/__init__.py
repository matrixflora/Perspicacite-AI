"""Data models for Perspicacité v2."""

from perspicacite.models.documents import ChunkMetadata, DocumentChunk
from perspicacite.models.kb import ChunkConfig, KBStats, KnowledgeBase
from perspicacite.models.messages import Conversation, Message, Session
from perspicacite.models.papers import Author, Paper, PaperSource, normalize_paper_dict
from perspicacite.models.rag import (
    RAGMode,
    RAGRequest,
    RAGResponse,
    SourceReference,
    StreamEvent,
)
from perspicacite.models.search import RetrievedChunk, SearchFilters, SearchQuery

__all__ = [
    "Author",
    "ChunkConfig",
    "ChunkMetadata",
    "Conversation",
    "DocumentChunk",
    "KBStats",
    "KnowledgeBase",
    "Message",
    "Paper",
    "PaperSource",
    "RAGMode",
    "RAGRequest",
    "RAGResponse",
    "RetrievedChunk",
    "SearchFilters",
    "SearchQuery",
    "Session",
    "SourceReference",
    "StreamEvent",
    "normalize_paper_dict",
]
