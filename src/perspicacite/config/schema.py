"""Pydantic models for configuration."""

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class ServerConfig(BaseModel):
    """Server configuration."""

    host: str = "0.0.0.0"
    port: int = Field(default=5468, ge=1024, le=65535)
    reload: bool = False

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5468",
        ]
    )


class MCPConfig(BaseModel):
    """MCP server configuration."""

    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = Field(default=5001, ge=1024, le=65535)
    transport: Literal["stdio", "sse", "streamable-http"] = "streamable-http"


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: Path = Field(default=Path("~/.local/share/perspicacite/memory.db"))
    chroma_path: Path = Field(default=Path("~/.local/share/perspicacite/chroma"))

    @field_validator("path", "chroma_path")
    @classmethod
    def expand_home(cls, v: Path) -> Path:
        """Expand ~ to home directory."""
        return v.expanduser()


class KnowledgeBaseConfig(BaseModel):
    """Knowledge base defaults."""

    embedding_model: str = "text-embedding-3-small"
    chunk_size: int = Field(default=1000, ge=100, le=10000)
    chunk_overlap: int = Field(default=200, ge=0, le=1000)
    chunking_method: Literal["token", "semantic", "agentic"] = "token"
    default_top_k: int = Field(default=10, ge=1, le=100)
    similarity_threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    use_two_pass: bool = Field(
        default=True, description="Enable two-pass retrieval for full paper context"
    )
    markdown_heading_aware: bool = Field(
        default=True,
        description="Use heading-aware chunking for markdown files",
    )
    code_language_aware: bool = Field(
        default=True,
        description="Use language-aware chunking for source-code files",
    )
    # Contextual retrieval — three tiers, pick by cost / quality:
    #
    #   "none"     — structural prefix only (title + section), free.
    #                Already happens unconditionally.
    #   "abstract" — also prepend the paper's abstract (or first ~500
    #                chars of full_text) to every chunk of that paper.
    #                Zero LLM calls; uses Crossref/OpenAlex metadata
    #                we already have. Same prefix on every chunk of a
    #                paper.
    #   "summary"  — one LLM call per paper to produce a 50-100 word
    #                summary, cached, applied to every chunk of that
    #                paper. 1/N the cost of "chunk" for N chunks.
    #   "chunk"    — Anthropic-style: one LLM call per chunk produces
    #                a contextual sentence specific to that chunk.
    #                Most expensive, best recall (30-40% lift on
    #                technical content per Anthropic's benchmark).
    #
    # The `contextual_retrieval: bool` field below stays for backwards
    # compat — True maps to "chunk", False maps to "none". The new
    # `contextual_retrieval_tier` overrides when set.
    contextual_retrieval_tier: Literal["none", "abstract", "summary", "chunk"] = Field(
        default="none",
        description="Contextual-retrieval cost/quality tier.",
    )
    contextual_retrieval: bool = Field(
        default=False,
        description="(legacy) shorthand for contextual_retrieval_tier='chunk'.",
    )
    contextual_retrieval_model: str = Field(
        default="claude-haiku-4-5",
        description="Cheap fast model for contextual-retrieval prefix generation.",
    )
    contextual_retrieval_provider: str = Field(
        default="anthropic",
        description="Provider for the contextual-retrieval model.",
    )
    contextual_retrieval_max_chars: int = Field(
        default=400,
        ge=0,
        le=2000,
        description="Max chars of LLM-generated context prepended per chunk.",
    )


class CopyrightFilterConfig(BaseModel):
    """Runtime check on synthesis output to catch verbatim copies of
    source-paper text. Defense-in-depth on top of the safeguard prompt
    that already nudges the LLM to paraphrase.

    Modes:
    - ``log``: warn-only, answer returned unchanged (always-on default).
    - ``quote``: wrap spans in “…” + citation (no LLM call).
    - ``strip``: replace spans with ``[content paraphrased]``.
    - ``rewrite``: LLM paraphrases the flagged spans (one extra call).
    """

    enabled: bool = Field(default=True, description="Run the copyright filter on synthesis output.")
    action: Literal["log", "quote", "strip", "rewrite"] = Field(
        default="log",
        description="What to do when a verbatim span is detected.",
    )
    min_ngram: int = Field(
        default=8, ge=3, le=30,
        description="Minimum word-ngram length to flag as verbatim.",
    )
    rewrite_model: str = "claude-haiku-4-5"
    rewrite_provider: str = "anthropic"


class LLMProviderConfig(BaseModel):
    """Configuration for an LLM provider."""

    base_url: str
    timeout: int = Field(default=60, ge=1)
    max_retries: int = Field(default=3, ge=0)


class LLMConfig(BaseModel):
    """LLM configuration."""

    default_provider: str = "deepseek"
    default_model: str = "deepseek-chat"  # DeepSeek V3
    # v1 core/core.py get_response: truncate mandatory + base system prompt to this length (chars)
    max_context_window: int = Field(default=10000, ge=2000, le=500000)

    providers: dict[str, LLMProviderConfig] = Field(
        default_factory=lambda: {
            "anthropic": LLMProviderConfig(
                base_url="https://api.anthropic.com",
                timeout=120,
                max_retries=3,
            ),
            "openai": LLMProviderConfig(
                base_url="https://api.openai.com/v1",
                timeout=60,
                max_retries=3,
            ),
            "deepseek": LLMProviderConfig(
                base_url="https://api.deepseek.com",
                timeout=60,
                max_retries=3,
            ),
            "minimax": LLMProviderConfig(
                base_url="https://api.minimaxi.com/anthropic",  # Anthropic-compatible API for Chinese users
                timeout=120,
                max_retries=3,
            ),
        }
    )

    context: dict[str, Any] = Field(
        default_factory=lambda: {
            "max_tokens": 8000,
            "chat_history_turns": 10,
            "summarize_threshold": 20,
        }
    )


class RAGModeSettings(BaseModel):
    """Settings for a RAG mode."""

    max_iterations: int = Field(default=1, ge=1)
    tools: list[str] = Field(default_factory=list)
    rerank: bool = True
    query_expansion: bool = False
    enable_planning: bool = False
    enable_reflection: bool = False
    build_citation_graph: bool = False
    use_hybrid: bool = False  # Use hybrid retrieval (vector + BM25)
    max_papers: int = Field(
        default=10, ge=1, le=50
    )  # Max papers to include in response (agentic mode)
    # v1 core: optional separate model for refine_response / evaluate_response
    evaluator_provider: Optional[str] = None
    evaluator_model: Optional[str] = None
    # v1 core refine_response / profonde (clamped in mode code to 1–3 where applicable)
    refinement_iterations: int = Field(default=2, ge=1, le=5)
    # v1 profonde: mid-cycle plan review after consecutive step failures
    enable_plan_review: bool = True
    # v1 get_response / profonde relevancy features
    use_relevancy_optimization: bool = True
    # Cap on per-paper LLM extraction calls during final map-reduce answer synthesis
    map_reduce_max_papers: int = Field(default=8, ge=1, le=64)


class RAGModesConfig(BaseModel):
    """RAG mode configurations for benchmark comparison."""

    reranker_model: str = Field(
        default="cross-encoder/ms-marco-MiniLM-L-6-v2",
        description="HuggingFace cross-encoder model used for reranking",
    )

    # KB auto-routing — triggered by `kb_name="auto"` in /api/chat or
    # MCP route_kbs(). The router scores each KB's description +
    # sampled paper titles against the query and picks top-N to query
    # in parallel via the multi-KB path.
    route_method: Literal["bm25", "llm"] = Field(
        default="bm25",
        description="bm25 = free + fast; llm = one cheap LLM call per route, better on semantics.",
    )
    route_top_k: int = Field(
        default=3, ge=1, le=10,
        description="Max KBs the router will return.",
    )
    route_threshold: float = Field(
        default=0.1, ge=0.0, le=1.0,
        description="Drop KBs whose normalized score is below this.",
    )

    # Basic: Single query, no refinement, fastest
    basic: RAGModeSettings = Field(
        default_factory=lambda: RAGModeSettings(
            max_iterations=1,
            tools=["kb_search"],
            rerank=False,
            query_expansion=False,
            enable_planning=False,
            enable_reflection=False,
        )
    )

    # Advanced: Query rephrasing, WRRF scoring, optional refinement
    advanced: RAGModeSettings = Field(
        default_factory=lambda: RAGModeSettings(
            max_iterations=1,
            tools=["kb_search"],
            rerank=True,
            query_expansion=True,  # Generate similar queries
            enable_planning=False,
            enable_reflection=True,  # Optional refinement
            use_hybrid=True,  # Enable hybrid retrieval by default
        )
    )

    # Profound: Multi-cycle research with planning (from v1)
    profound: RAGModeSettings = Field(
        default_factory=lambda: RAGModeSettings(
            max_iterations=3,
            tools=["kb_search", "web_search"],
            rerank=True,
            query_expansion=True,
            enable_planning=True,  # Research planning
            enable_reflection=True,  # Plan review and adjustment
        )
    )

    # Agentic: Intent-based with tool selection
    agentic: RAGModeSettings = Field(
        default_factory=lambda: RAGModeSettings(
            max_iterations=5,
            tools=["kb_search", "lotus_search", "literature_search", "fetch_pdf"],
            rerank=True,
            query_expansion=True,
            enable_planning=True,
            enable_reflection=True,
        )
    )

    # Contradiction: Identify agreement / disagreement / open questions across papers
    contradiction: RAGModeSettings = Field(
        default_factory=lambda: RAGModeSettings(
            max_iterations=1,
            tools=["kb_search"],
            rerank=True,
            query_expansion=True,
            enable_planning=False,
            enable_reflection=False,
            use_hybrid=True,
        )
    )


class SciLexAPIConfig(BaseModel):
    """Configuration for a SciLEx API."""

    enabled: bool = True
    rate_limit: int = Field(default=100, ge=1)


class SciLexConfig(BaseModel):
    """SciLEx integration configuration."""

    enabled: bool = True
    config_path: Optional[Path] = None
    pubmed_email: str = ""

    apis: dict[str, SciLexAPIConfig] = Field(
        default_factory=lambda: {
            "semantic_scholar": SciLexAPIConfig(enabled=True, rate_limit=100),
            "openalex": SciLexAPIConfig(enabled=True, rate_limit=100),
            "pubmed": SciLexAPIConfig(enabled=True, rate_limit=10),
            "arxiv": SciLexAPIConfig(enabled=True, rate_limit=100),
            "ieee": SciLexAPIConfig(enabled=False, rate_limit=100),
            "springer": SciLexAPIConfig(enabled=False, rate_limit=100),
            "elsevier": SciLexAPIConfig(enabled=False, rate_limit=100),
            "hal": SciLexAPIConfig(enabled=True, rate_limit=100),
            "dblp": SciLexAPIConfig(enabled=True, rate_limit=100),
            "istex": SciLexAPIConfig(enabled=True, rate_limit=100),
        }
    )

    collection: dict[str, Any] = Field(
        default_factory=lambda: {
            "default_max_papers": 100,
            "quality_threshold": 0.7,
            "deduplicate": True,
            "download_pdfs": True,
        }
    )


class WebSearchConfig(BaseModel):
    """Web search configuration."""

    providers: list[str] = Field(default_factory=lambda: ["google_scholar", "semantic_scholar"])
    cache_ttl: int = Field(default=3600, ge=0)  # seconds


class ZoteroConfig(BaseModel):
    """Zotero integration configuration."""

    enabled: bool = False
    api_key: str = ""
    library_id: str = ""
    library_type: str = "user"  # "user" or "group"
    collection_key: str = ""
    # Base URL for the Zotero API. Empty = cloud (api.zotero.org). To use
    # the desktop app's local API (which serves attachments from local
    # storage — including Linked Files), set to
    # "http://localhost:23119/api" and enable
    # "Allow other applications on this computer to communicate with Zotero"
    # in Zotero's Settings → Advanced. api_key may be omitted when base_url
    # is on loopback.
    base_url: str = ""


class LocalDocsConfig(BaseModel):
    """Server-side local-document ingestion configuration.

    `allowed_roots` is the allow-list for the `/api/kb/{name}/local-paths`
    endpoint and the `ingest_local_documents` MCP tool. If empty, those
    endpoints/tools refuse all calls (server-side path entry is disabled).
    The web multipart upload path (`/api/kb/{name}/local-files`) is
    unaffected.
    """

    allowed_roots: list[Path] = Field(default_factory=list)

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def _expand_roots(cls, v: Any) -> list[Path]:
        if v is None:
            return []
        out: list[Path] = []
        for p in v:
            out.append(Path(p).expanduser().resolve())
        return out


class CapsuleConfig(BaseModel):
    """Per-paper capsule storage and build behaviour."""

    enabled: bool = True
    auto_build_on_ingest: bool = True
    root: Path = Path("./data/capsules")
    min_version: str = "0.1"


class MultimodalConfig(BaseModel):
    """Multimodal RAG: figures-in-prompt + inline thumbnails in answers."""

    enabled: bool = True
    max_images: int = 6
    vision_allowlist: list[str] = Field(
        default_factory=lambda: [
            "anthropic/claude-",
            "claude-",
            "openai/gpt-4o",
            "gpt-4o",
        ]
    )


class ExternalResourcesConfig(BaseModel):
    """V1 mining + V2 fetch-on-demand for paper-referenced external resources."""

    mine: bool = True                      # V1 — always-on (Cycle A wires this)
    fetch_on_demand: bool = True           # V2 — gated by user/MCP action
    cache_dir: Path = Path("./data/cache")
    cache_ttl_days: int = 30
    zenodo_max_bytes_per_file: int = 500_000
    zenodo_max_bytes_per_record: int = 5_000_000
    text_file_extensions: list[str] = Field(default_factory=lambda: [
        ".md", ".rst", ".txt",
        ".py", ".R", ".r", ".jl",
        ".ipynb",
        ".yml", ".yaml", ".toml", ".json", ".csv",
    ])


class PDFDownloadConfig(BaseModel):
    """PDF download configuration."""

    unpaywall_email: Optional[str] = Field(
        default=None, description="Email for Unpaywall API. Required for querying open access PDFs."
    )
    alternative_endpoint: Optional[str] = Field(
        default=None,
        description="Alternative endpoint for PDF downloads (e.g., Sci-Hub mirror). User must provide their own.",
    )

    # Publisher API keys for institutional access
    # Open access (no key needed)
    # - arXiv: fully open, no registration needed

    # Institutional access (API keys needed)
    wiley_tdm_token: Optional[str] = Field(
        default=None,
        description="Wiley TDM (Text and Data Mining) API client token. Register at https://developer.wiley.com/",
    )
    elsevier_api_key: Optional[str] = Field(
        default=None,
        description="Elsevier ScienceDirect API key. Register at https://dev.elsevier.com/",
    )
    aaas_api_key: Optional[str] = Field(
        default=None, description="AAAS (Science) API key for institutional access."
    )
    rsc_api_key: Optional[str] = Field(
        default=None,
        description="Royal Society of Chemistry API key. Register at https://api.rsc.org/",
    )
    springer_api_key: Optional[str] = Field(
        default=None,
        description="Springer Nature API key. Register at https://dev.springernature.com/",
    )
    # ACS typically uses IP-based access, no API key

    semantic_scholar_api_key: Optional[str] = Field(
        default=None,
        description="Semantic Scholar API key. Register at https://www.semanticscholar.org/product/api#api-key",
    )

    timeout: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=3, ge=0)

    # Cookie jar for institutional-access PDF fetch.
    # Path to a Netscape-format cookies.txt exported from a browser
    # logged into the user's library proxy (e.g. via the "Get
    # cookies.txt" / "EditThisCookie" extension). When set, those
    # cookies are attached to every PDF-download request so the
    # publisher serves the entitled PDF over the user's session.
    # This is the server-side equivalent of what the Zotero Connector
    # extension does in the browser. Cookies expire — re-export as
    # needed.
    cookies_path: Optional[str] = Field(
        default=None,
        description="Path to Netscape-format cookies.txt for institutional PDF access.",
    )
    cookie_domains: list[str] = Field(
        default_factory=list,
        description=(
            "Only attach the cookie jar to requests whose host matches one "
            "of these substrings (e.g. ['sciencedirect.com', 'wiley.com', "
            "'proxy.lib.example.edu']). Empty list = attach to all PDF "
            "requests (broadest access, slight risk of cookie leakage to "
            "third-party hosts in the redirect chain)."
        ),
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    format: Literal["json", "text"] = "json"


class AuthConfig(BaseModel):
    """Authentication configuration."""

    enabled: bool = True
    token: Optional[str] = None  # Set via env: PERSPICACITE_AUTH_TOKEN


class UIConfig(BaseModel):
    """UI configuration."""

    theme: Literal["light", "dark", "system"] = "system"
    citation_format: Literal["nature", "apa", "mla", "ieee"] = "nature"


class Config(BaseModel):
    """Main configuration for Perspicacité v2."""

    version: str = "2.0.0"
    config_name: str = "default"

    server: ServerConfig = Field(default_factory=ServerConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    knowledge_base: KnowledgeBaseConfig = Field(default_factory=KnowledgeBaseConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    rag_modes: RAGModesConfig = Field(default_factory=RAGModesConfig)
    scilex: SciLexConfig = Field(default_factory=SciLexConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    pdf_download: PDFDownloadConfig = Field(default_factory=PDFDownloadConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    zotero: ZoteroConfig = Field(default_factory=ZoteroConfig)
    local_docs: LocalDocsConfig = Field(default_factory=LocalDocsConfig)
    capsule: CapsuleConfig = Field(default_factory=CapsuleConfig)
    multimodal: MultimodalConfig = Field(default_factory=MultimodalConfig)
    external_resources: ExternalResourcesConfig = Field(default_factory=ExternalResourcesConfig)
    copyright_filter: CopyrightFilterConfig = Field(default_factory=CopyrightFilterConfig)

    @model_validator(mode="after")
    def validate_config(self) -> "Config":
        """Validate configuration consistency."""
        # Ensure chunk_overlap < chunk_size
        if self.knowledge_base.chunk_overlap >= self.knowledge_base.chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        return self

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        """Validate version string."""
        if not v.startswith("2."):
            raise ValueError("Config version must be 2.x")
        return v

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict, masking secrets."""
        d = self.model_dump()
        # Mask auth token if present
        if self.auth.token:
            d["auth"]["token"] = "***"
        return d
