"""Application state for the web app.

Holds the singleton instance of `AppState` that all routers share. Routers
import `app_state` from this module; the FastAPI lifespan calls
`await app_state.initialize()` once on startup.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from perspicacite.memory.session_store import SessionStore
from perspicacite.provenance.store import ProvenanceStore
from perspicacite.jobs.registry import JobRegistry


logger = logging.getLogger(__name__)


class AppState:
    """Application state with agentic orchestrator and RAG engine."""

    def __init__(self):
        self.llm_client = None
        self.embedding_provider = None
        self.vector_store = None
        self.orchestrator = None
        self.rag_engine = None  # Multi-mode RAG engine
        self.session_store: Optional[SessionStore] = None
        self.provenance_store: Optional[ProvenanceStore] = None
        self.job_registry: Optional[JobRegistry] = None
        self.pdf_downloader = None
        self.pdf_parser = None
        self.initialized = False

    async def initialize(self):
        """Initialize all components."""
        if self.initialized:
            return

        logger.info("Initializing Perspicacité v2 Agentic System...")

        # Load config
        from perspicacite.config.loader import load_config
        from perspicacite.llm import AsyncLLMClient, LiteLLMEmbeddingProvider
        from perspicacite.retrieval import ChromaVectorStore
        from perspicacite.rag.agentic import AgenticOrchestrator, LLMAdapter
        from perspicacite.rag.tools import ToolRegistry, LotusSearchTool

        config = load_config()

        # Initialize LLM
        self.llm_client = AsyncLLMClient(config.llm)
        logger.info("LLM client initialized")

        # Initialize embeddings
        self.embedding_provider = LiteLLMEmbeddingProvider(
            model=config.knowledge_base.embedding_model,
        )
        logger.info("Embedding provider initialized")

        # Initialize vector store
        self.vector_store = ChromaVectorStore(
            persist_dir="./chroma_db", embedding_provider=self.embedding_provider
        )
        logger.info("Vector store initialized")

        # Store config for later use
        self.config = config

        # Initialize tool registry (LOTUS deactivated for now)
        tool_registry = ToolRegistry()
        logger.info("Tool registry initialized (LOTUS deactivated)")

        # Create LLM adapter for agentic components
        llm_adapter = LLMAdapter(
            client=self.llm_client,
            model=config.llm.default_model,
            provider=config.llm.default_provider,
        )

        # Initialize agentic orchestrator
        self.orchestrator = AgenticOrchestrator(
            llm_client=llm_adapter,
            tool_registry=tool_registry,
            embedding_provider=self.embedding_provider,
            vector_store=self.vector_store,
            max_iterations=5,
            use_two_pass=getattr(config.knowledge_base, "use_two_pass", True),
            map_reduce_max_papers=getattr(config.rag_modes.agentic, "map_reduce_max_papers", 8),
        )
        logger.info("Agentic orchestrator initialized")

        # Initialize RAG engine for multi-mode support
        from perspicacite.rag.engine import RAGEngine

        self.rag_engine = RAGEngine(
            llm_client=self.llm_client,
            vector_store=self.vector_store,
            embedding_provider=self.embedding_provider,
            tool_registry=tool_registry,
            config=config,
        )
        logger.info("RAG engine initialized (supports all modes)")

        # Initialize session store (SQLite for KB metadata)
        db_path = Path("./data/perspicacite.db")
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_store = SessionStore(db_path)
        await self.session_store.init_db()
        logger.info("Session store initialized")

        sidecar_dir = self.session_store.db_path.parent / "provenance"
        self.provenance_store = ProvenanceStore(
            db_path=self.session_store.db_path,
            sidecar_dir=sidecar_dir,
        )
        logger.info("Provenance store initialized")
        self.rag_engine.provenance_store = self.provenance_store

        self.job_registry = JobRegistry(db_path=self.session_store.db_path)
        logger.info("Job registry initialized")

        # Initialize PDF downloader and parser
        from perspicacite.pipeline.download import PDFDownloader
        from perspicacite.pipeline.parsers.pdf import PDFParser

        self.pdf_downloader = PDFDownloader()
        self.pdf_parser = PDFParser()
        logger.info("PDF downloader and parser initialized")

        self.initialized = True
        logger.info("System initialization complete!")


app_state = AppState()
