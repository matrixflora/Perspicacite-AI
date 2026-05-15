# Architecture

This document is a code tour of Perspicacité's layered architecture. For the
higher-level design rationale, see [`docs/VISION.md`](../VISION.md#3-architecture-overview).

---

## Entry points

| Entry point | File | Description |
|------------|------|-------------|
| CLI | `src/perspicacite/cli.py` | Click command group; `main()` is the package entry point |
| Web server | `src/perspicacite/web/app.py` | FastAPI `app`; lifespan initializes AppState |
| MCP server | `src/perspicacite/mcp/server.py` | FastMCP server; shares AppState with the web app |

The `serve` CLI command starts both the FastAPI app and the MCP server in the same
asyncio event loop via `_start_mcp_and_web()`.

---

## AppState singleton

`src/perspicacite/web/state.py` defines `AppState`, a singleton initialized at startup
that holds:

- `config` — the loaded `Config` Pydantic model
- `session_store` — SQLite async client (aiosqlite)
- `vector_store` — ChromaDB client
- `rag_engine` — `RAGEngine` instance
- `job_store` — in-memory async job registry for SSE progress streaming

All router handlers and MCP tools receive `AppState` via FastAPI's dependency
injection or MCP's lifecycle context.

---

## Ingestion pipeline

The unified content pipeline lives in `src/perspicacite/pipeline/download/`:

```
unified.py          retrieve_paper_content(doi) → PaperContent
  ├── discovery.py  discover_paper(doi) → {pmcid, arxiv_id, oa_url, abstract}
  ├── europepmc.py  fetch_pmc_jats(pmcid) → structured text + references
  ├── arxiv.py      fetch_arxiv_html(arxiv_id) → sections + references
  ├── crossref.py   crossref_enrich(doi) → metadata, source=CROSSREF
  └── parsers/
      └── pdf.py    parse_pdf(bytes) → raw text
```

`retrieve_paper_content` is the single entry point for all content fetching. It is
called by the BibTeX ingestion pipeline (`pipeline/bibtex_kb.py`), the DOI-bulk
endpoint (`web/routers/kb.py`), and the agentic RAG mode's tool registry.

---

## RAG engine

`src/perspicacite/rag/engine.py` — `RAGEngine.generate(request: RAGRequest) → RAGResponse`.

The engine dispatches to a mode handler:

```
rag/
  engine.py          RAGEngine — dispatches by mode
  modes/
    basic.py         BasicRAGMode
    advanced.py      AdvancedRAGMode
    profound.py      ProfoundRAGMode
    agentic.py       AgenticRAGMode
    literature_survey.py  LiteratureSurveyMode
    contradiction.py  ContradictionMode
  tools/
    kb_search.py     KB search tool (wraps MultiKBRetriever)
    ...
```

Each mode handler has the same interface: it receives the `RAGRequest`, runs its
retrieval/synthesis logic, and returns a `RAGResponse` with `answer`, `sources`, and
`provenance`.

---

## Retrieval layer

`src/perspicacite/retrieval/`:

- `chroma_store.py` — wrapper around the ChromaDB client; handles collection
  creation, document upsert, and similarity search
- `multi_kb.py` — `MultiKBRetriever` fans a query across multiple Chroma collections
  and merges results by WRRF (Weighted Reciprocal Rank Fusion)
- `bm25.py` — BM25 scoring over the in-memory chunk text
- `recency.py` — `apply_recency_weighting()` applies exponential decay by publication
  year to re-rank BM25 + vector results

---

## Search layer

`src/perspicacite/search/`:

- `scilex_adapter.py` — wraps SciLEx's multi-database search and normalizes results
  to `Paper` objects with appropriate `PaperSource` values
- `screening.py` — `screen_papers()` runs BM25 or LLM scoring over a candidate list
- `pubmed.py` — `PubMedSearchAdapter` wraps Biopython's Entrez client
- `snowball.py` — citation-graph snowball walker (OpenAlex + Semantic Scholar fallback)

---

## LLM routing

`src/perspicacite/llm/`:

- `client.py` — `LLMClient` dispatches to LiteLLM for direct API calls, with Anthropic
  prompt caching enabled on the hottest call sites
- `agent_cli.py` — `AgentCLIClient` wraps a subprocess call to a one-shot CLI agent
- `mcp_sampling.py` — `MCPSamplingClient` attempts to use `sampling/createMessage` via
  the connected MCP client, with fall-through to LiteLLM on failure

---

## Web routers

`src/perspicacite/web/routers/`:

| Router | Prefix | Description |
|--------|--------|-------------|
| `health.py` | `/api/health` | Liveness check |
| `chat.py` | `/api/chat` | RAG synthesis, SSE streaming |
| `kb.py` | `/api/kb` | KB CRUD, paper ingest, export |
| `conversations.py` | `/api/conversations` | Conversation history, provenance, export |
| `jobs.py` | `/api/jobs` | Async job status and SSE events |
| `zotero.py` | `/api/zotero` | Zotero status and push |
| `zotero_ingest.py` | `/api/zotero-ingest` | Build KBs from Zotero collections |
| `survey.py` | `/api/survey` | Literature survey session management |

---

## Data models

`src/perspicacite/models/`:

- `papers.py` — `Paper`, `Author`, `PaperSource` enum
- `kb.py` — `KnowledgeBase`, `Chunk`
- `rag.py` — `RAGRequest`, `RAGResponse`, `ProvenanceRecord`
- `documents.py` — `Document` (local-file ingest model)

---

## Related topics

- [`docs/VISION.md`](../VISION.md) — higher-level architecture overview
- [development/testing.md](testing.md) — test structure that mirrors this layout
- [reference/paper-source-enum.md](../reference/paper-source-enum.md) — `PaperSource`
  values assigned across the pipeline
