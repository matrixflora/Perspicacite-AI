<div align="center">

# Perspicacité — AI-Powered Scientific Literature Research Assistant

*Local-first RAG system for searching, understanding, and organizing academic literature*

**6 RAG modes** &nbsp;·&nbsp; **Unified content pipeline** &nbsp;·&nbsp; **Hybrid vector+BM25 retrieval** &nbsp;·&nbsp; **MCP server** &nbsp;·&nbsp; **REST API**

[![Paper](https://img.shields.io/badge/Paper-ISWC--C%202025-blue?style=flat-square)](https://iswc2025.semanticweb.org/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg?style=flat-square)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/Python-3.12%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)

</div>

---

**Perspicacité** (French for "insight") helps scientists, researchers, and students search, understand, and organize academic literature using AI grounded in real research papers. It works entirely on your machine — only LLM inference calls leave your environment.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [How to Use Perspicacité](#how-to-use-perspicacité)
- [RAG Modes](#rag-modes)
- [Content Retrieval Pipeline](#content-retrieval-pipeline)
- [MCP Server](#mcp-server)
- [REST API](#rest-api)
- [Integration with AI Agents](#integration-with-ai-agents)
- [CLI Commands](#cli-commands)
- [Configuration](#configuration)
- [Knowledge Bases](#knowledge-bases)
- [Development](#development)
- [Privacy & Data](#privacy--data)
- [Contributing](#contributing)
- [License](#license)
- [Citation](#citation)

---

## Features

- **Multi-database search** — Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, DBLP, and more via SciLEx
- **Unified content pipeline** — Retrieves structured full text (PMC JATS XML, arXiv HTML), PDFs, or abstracts with quality-based priority routing
- **6 RAG modes** — From fast KB retrieval to multi-cycle agentic research, systematic literature surveys, and cross-paper contradiction detection
- **Knowledge base management** — Import from BibTeX, add papers by DOI, semantic search within your collections
- **MCP server** — 18 tools exposed via Model Context Protocol for integration with AI agents (Mimosa-AI, SmolAgents, etc.)
- **REST API** — Full JSON API for chat, KB management, conversations, and literature surveys
- **Provenance tracking** — Per-answer trace (retrieved chunks, mode, model, latency) stored in SQLite and exportable as RO-Crate 1.1 zip bundles
- **Institutional-access PDFs** — Ride your browser's logged-in session via `perspicacite import-browser-cookies`; paywalled journals your institution licenses become reachable server-side
- **Auto KB routing** — Send a chat with `kb_name: "auto"` and Perspicacité scores every KB's description + sampled titles against the query (BM25 free; LLM tier optional), then queries the top-N in parallel via the multi-KB path
- **Zotero integration** — Push to cloud, or point at the desktop app's local API to reach Linked Files / non-cloud-synced PDFs
- **Obsidian vault export** — Export any KB as an Obsidian-compatible Markdown vault
- **Async ingestion** — Long BibTeX / DOI import jobs run in the background with SSE progress streaming
- **Local-first** — Data stays on your machine; only API calls go to LLM providers

---

## Quick Start

### Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip

### Install

```bash
git clone https://github.com/HolobiomicsLab/Perspicacite-AI.git
cd Perspicacite-AI
uv sync
```

### Configure

```bash
cp config.example.yml config.yml
cp .env.example .env
# Edit .env — add at least one LLM API key
```

| Provider | Get a key at |
|----------|-------------|
| **DeepSeek** (default) | [platform.deepseek.com](https://platform.deepseek.com/) |
| **OpenAI** | [platform.openai.com](https://platform.openai.com/) |
| **Anthropic** | [console.anthropic.com](https://console.anthropic.com/) |

### Run

```bash
uv run perspicacite -c config.yml serve
```

Open **http://localhost:8000** in your browser. The MCP server runs on the same port at `/mcp`. Use `--no-mcp` to disable it.

---

## How to Use Perspicacité

### 1. Choose Your Knowledge Base (or Don't)

In the left sidebar, under **"Knowledge Base"**:

- **"No KB (web search only)"** — searches academic databases live for every query
- **Your own KBs** — searches only papers you have added

**Create a Knowledge Base:**
1. Click "+ Create new KB"
2. Enter a name and drag-and-drop a `.bib` file
3. Click "Create from BibTeX" to import papers and index them

### 2. Ask a Question

Type your research question in the chat box. Examples:

- *"What are the effects of green tea extract on metabolism?"*
- *"How is feature-based molecular networking used in metabolomics?"*
- *"Compare transformer models to CNNs for medical imaging"*

### 3. Choose a Mode

Select a RAG mode from the dropdown. See the [RAG Modes](#rag-modes) table below for guidance on which to use.

### 4. Review the Answer

Perspicacité will:
1. Show its thinking process (click to expand)
2. Search and score relevant papers
3. Download full texts when possible
4. Generate an answer with citations

### 5. Save Interesting Papers

At the bottom of each response, click **"Add to KB"** on any paper to save it to your knowledge base.

---

## RAG Modes

| Mode | Description | Best For | Speed |
|------|-------------|----------|-------|
| **Basic** | Single-query hybrid vector+BM25 retrieval from your KB | Well-curated KB, quick answers | Fast |
| **Advanced** | Query expansion, WRRF fusion scoring, reranking | Broader KB search, better precision | Medium |
| **Profound** | Multi-cycle research (up to 3 iterations) with planning and self-evaluation | Complex questions, multiple perspectives | Slower |
| **Agentic** | Intent-based agent with tool use (web search, PDF download), up to 5 iterations | Questions requiring live discovery beyond your KB | Variable |
| **Literature Survey** | Systematic field mapping: broad search, theme clustering, AI recommendations | Mapping a research field, exploring a new topic | Slowest |
| **Contradiction** | Multi-paper claim clustering into agreement / disagreement / open-question buckets | Comparing conflicting findings across papers | Medium |

---

## Content Retrieval Pipeline

Paper content is retrieved through a unified pipeline with quality-based priority routing:

```
1. Discovery      — OpenAlex + Unpaywall → PMCID, arXiv ID, OA status, abstract
2. Structured     — PMC JATS XML → Europe PMC → arXiv HTML (sections + references)
3. PDF full text  — OA PDF, arXiv PDF, Unpaywall, publisher APIs (ACS, Springer, Wiley, Elsevier, …)
4. Abstract only  — from discovery metadata when no full text is available
5. Discard        — returns failure for papers with no retrievable content
```

Structured content (PMC, arXiv) provides sections and references. PDF content provides raw text via PyMuPDF. Papers behind paywalls with no OA version are served as abstracts. The `content_type` field in results is `"structured"` > `"full_text"` > `"abstract"` > `"none"`.

---

## MCP Server

Perspicacité exposes an MCP server with 18 tools at `http://localhost:8000/mcp`, accessible via:
- **MCP protocol** — native tool discovery and invocation
- **HTTP JSON-RPC** — `POST /mcp` with standard JSON-RPC 2.0 envelope

### Tools

| Tool | Description |
|------|-------------|
| `search_literature` | Search academic databases with year range and article-type filters |
| `get_paper_content` | Fetch full text + sections by DOI through the unified pipeline |
| `get_paper_references` | Extract cited references from a paper |
| `create_knowledge_base` | Create a new KB |
| `add_papers_to_kb` | Add papers with auto-download and indexing |
| `add_dois_to_kb` | Bulk-add papers to a KB from a list of DOIs (max 200 per call) |
| `search_knowledge_base` | Semantic search within a KB |
| `list_knowledge_bases` | List all KBs with stats |
| `generate_report` | Synthesize a research report using RAG |
| `screen_papers` | Score candidate papers by relevance to a query (BM25 or LLM-rated) |
| `push_to_zotero` | Push a list of DOIs to a configured Zotero library |
| `build_kbs_from_zotero` | Build one KB per Zotero top-level collection (per-call `library_id` override) |
| `ingest_local_documents` | Server-side ingest of local PDFs / docs under configured allowlist roots |
| `build_capsule` | Build per-paper Capsule (figures + structured text + mined resources) |
| `build_capsules_for_kb` | Build Capsules for every paper in a KB (idempotent) |
| `fetch_paper_resources` | Fetch GitHub / Zenodo / Crossref / Unpaywall / PubMed external resources |
| `fetch_supplementary` | Download Supplementary Information files (PMC OA S3 → Springer ESM → ACS) |
| `route_kbs` | Pick the most-relevant KBs for a query (BM25 or LLM) — pass results to `kb_names` |

Full usage details and parameter documentation: [`docs/perspicacite_skills.md`](docs/perspicacite_skills.md)

### Example: JSON-RPC Call

```python
import httpx

# Initialize session
r = httpx.post("http://localhost:8000/mcp", json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
               "clientInfo": {"name": "my-agent", "version": "1.0"}}
}, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
session_id = r.headers["mcp-session-id"]

# Call a tool
r = httpx.post("http://localhost:8000/mcp", json={
    "jsonrpc": "2.0", "id": 2, "method": "tools/call",
    "params": {"name": "search_literature", "arguments": {"query": "flash attention"}}
}, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": session_id})
```

---

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/chat` | Chat endpoint (streaming SSE or non-streaming JSON) |
| `GET` | `/api/health` | Health check |
| `GET` | `/api/conversations` | List conversations |
| `POST` | `/api/conversations` | Create conversation |
| `DELETE` | `/api/conversations/{id}` | Delete conversation |
| `GET` | `/api/conversations/search?q=...` | Full-text search across conversations (FTS5) |
| `GET` | `/api/conversations/{id}/export?format=markdown` | Download conversation as Markdown |
| `GET` | `/api/conversations/{id}/export?format=ro-crate` | Download conversation + provenance as RO-Crate 1.1 zip |
| `GET` | `/api/conversations/{conv_id}/messages/{msg_id}/provenance` | Provenance trace for a single answer |
| `GET` | `/api/conversations/{conv_id}/provenance` | All provenance records for a conversation |
| `GET` | `/api/kb` | List knowledge bases |
| `POST` | `/api/kb` | Create KB |
| `GET` | `/api/kb/{name}` | Get KB details |
| `DELETE` | `/api/kb/{name}` | Delete KB |
| `GET` | `/api/kb/{name}/stats` | KB statistics (paper/chunk counts, year histogram, sources) |
| `POST` | `/api/kb/{name}/papers` | Add papers to KB |
| `POST` | `/api/kb/{name}/bibtex` | Import from BibTeX (synchronous) |
| `POST` | `/api/kb/{name}/bibtex/async` | Import from BibTeX (async job, SSE progress) |
| `POST` | `/api/kb/{name}/dois` | Bulk-add papers by DOI list (synchronous) |
| `POST` | `/api/kb/{name}/dois/async` | Bulk-add papers by DOI list (async job, SSE progress) |
| `GET` | `/api/kb/{name}/export?format=obsidian-vault` | Download KB as Obsidian Markdown vault zip |
| `GET` | `/api/jobs/{id}` | Check async ingestion job status |
| `GET` | `/api/jobs/{id}/events` | SSE stream of async job progress events |
| `GET` | `/api/zotero/status` | Check Zotero integration status |
| `POST` | `/api/zotero/push` | Push papers to Zotero by DOI list |
| `GET` | `/api/paper?doi=...` | Fetch discovery metadata + content-type availability for a DOI |
| `GET` | `/api/survey/{session_id}` | Get literature survey status |
| `POST` | `/api/survey/{session_id}/generate` | Generate survey report |

Pass `"stream": false` to `/api/chat` to get a JSON response instead of server-sent events.

### Auto KB routing — `kb_name: "auto"`

When you don't want to pick a KB by hand, send `kb_name: "auto"` (or
`kb_names: ["auto"]`). Perspicacité scores every KB's description +
sampled paper titles against your query and queries the top-N most
relevant in parallel.

```bash
curl -sN -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "metabolomics annotation methods", "kb_name": "auto", "mode": "basic", "stream": true}'
```

Look for a `kb_route` SSE event near the start of the stream — it
shows which KBs the router picked and their scores:

```json
{"type": "kb_route", "method": "bm25",
 "hits": [
   {"kb_name": "MetaboLinkAI_final_Unfiled", "score": 1.000, "sampled_titles": 12},
   {"kb_name": "Library_AI-FORGE",          "score": 0.668, "sampled_titles": 12}]}
```

Tune in `config.yml`:
```yaml
rag_modes:
  route_method: "bm25"   # bm25 = free + fast; llm = one cheap LLM call
  route_top_k: 3
  route_threshold: 0.1
```

For introspection without running synthesis, use the MCP `route_kbs`
tool — same scoring, returns just the ranked hits.

---

## Integration with AI Agents

Perspicacité is designed to be used as a scientific grounding companion by autonomous AI agents:

**[Mimosa-AI](https://github.com/HolobiomicsLab/Mimosa-AI)** — a self-evolving multi-agent framework for autonomous scientific research — integrates natively with Perspicacité via its MCP interface. When Perspicacité is running, Mimosa automatically calls its literature search and KB tools to ground workflow creation and evaluation in peer-reviewed literature.

To use Perspicacité with Mimosa:
1. Start Perspicacité: `uv run perspicacite -c config.yml serve`
2. Start Mimosa separately and point it at `http://localhost:8000/mcp`

For **SmolAgents** or any MCP-compatible agent framework, add the MCP server URL to your agent's tool discovery configuration.

---

## CLI Commands

All CLI commands except `serve` route structured logs to stderr, so
stdout stays a clean stream you can pipe into `jq`, `tee`, etc.

```bash
# Start the server (web UI + MCP)
perspicacite -c config.yml serve [--host 0.0.0.0] [--port 8000] [--no-mcp] [--reload]

# List all KBs (sorted by paper count; --json for machine-readable)
perspicacite list-kb
perspicacite list-kb --json | jq '.[] | {name, paper_count}'

# Create an empty KB (add papers later via add-to-kb or the REST API)
perspicacite create-kb my-kb [--description "..."]

# Create a KB from BibTeX (downloads PDFs + indexes in one shot)
perspicacite -c config.yml create-kb my-kb --from-bibtex papers.bib

# Ask a question against a KB (full RAG via the same engine the web uses)
perspicacite query "what methods does this paper use?" --kb my-kb --mode basic
# Modes: basic | advanced | profound | contradiction

# Screen candidate papers by relevance (BM25; no server needed)
perspicacite -c config.yml screen-papers --input refs.bib --candidates cand.bib --output out.bib [--threshold 0.3] [--csv]

# Search PubMed and export to BibTeX (no server needed)
perspicacite -c config.yml pubmed-search --query "microbiome" --max-results 50 --output hits.bib

# Export browser cookies for institutional-access PDF downloads
# (requires the `cookies` extras: uv pip install -e ".[cookies]")
perspicacite import-browser-cookies --browser brave \
    --domain nature.com --domain wiley.com \
    --output ~/.config/perspicacite/cookies.txt

# Show version
perspicacite version
```

---

## Configuration

Copy and edit `config.example.yml`. Key sections:

```yaml
llm:
  default_provider: "deepseek"   # deepseek, openai, anthropic
  default_model: "deepseek-chat"

knowledge_base:
  embedding_model: "text-embedding-3-small"
  chunk_size: 1000
  chunk_overlap: 200
  chunking_method: "token"       # token, semantic, agentic

pdf_download:
  unpaywall_email: "your@email.com"
  # Optional publisher API keys:
  # elsevier_api_key: "..."
  # springer_api_key: "..."
  # wiley_tdm_token: "..."

mcp:
  enabled: true
```

Academic database search APIs are configured under `scilex:` — enabled sources include Semantic Scholar, OpenAlex, PubMed, arXiv, HAL, and DBLP by default.

### Cheap / local-only mode (zero API cost)

To run Perspicacité with no paid API calls — useful for dev, CI, or on
an air-gapped machine — point both the LLM and the embedding model at
local backends:

```yaml
llm:
  default_provider: "ollama"
  default_model: "llama3.1"          # or mistral, phi3
  providers:
    ollama:
      base_url: "http://localhost:11434"
      timeout: 120

knowledge_base:
  embedding_model: "all-MiniLM-L6-v2"  # local sentence-transformers (~80MB, 384-dim)
```

Then start Ollama (`brew install ollama && ollama serve && ollama pull llama3.1`)
and launch Perspicacité as usual.

**Caveats:**
- Ollama's tool-use support is limited, so agentic mode is best avoided —
  basic/advanced/contradiction modes work fine.
- A given KB is bound to the embedding model that wrote it. Cheap-mode
  only makes sense for **fresh KBs**; you can't query a KB built with
  OpenAI embeddings using a local model (different vector spaces).
- Local sentence-transformers will download once on first use (the
  `~/.cache/torch/sentence_transformers/` directory).

### Zotero secrets via environment variables

Instead of putting `zotero.api_key` in `config.yml`, you can set:

```bash
export ZOTERO_API_KEY=...                       # or PERSPICACITE_ZOTERO_API_KEY
export PERSPICACITE_ZOTERO_LIBRARY_ID=5691738   # optional override
export PERSPICACITE_ZOTERO_BASE_URL="http://localhost:23119/api"   # use local Zotero
```

These environment overrides take precedence over `config.yml`.

### Multi-database literature search (optional: SciLEx)

`search_literature` (MCP) and the agentic mode's online search rely on
**[SciLEx](https://github.com/datalogism/SciLEx)** — a MIT-licensed
multi-DB academic-search aggregator (Semantic Scholar, OpenAlex,
PubMed, arXiv, HAL, DBLP, IEEE, Springer). It's not on PyPI; install
it as an extra from GitHub:

```bash
uv pip install -e ".[scilex]"
```

Without SciLEx, `search_literature` returns a clear "not installed"
error and the agentic search step skips external lookups. All KB-side
tools (`search_knowledge_base`, `generate_report`, `add_dois_to_kb`)
work without SciLEx.

**Heads-up:** SciLEx pins versions of `beautifulsoup4`, `bibtexparser`,
`lxml`, etc. Installing it inside Perspicacité's venv may bump those
for the whole environment.

### Institutional-access PDFs via browser cookies

For papers behind a publisher paywall that your institution licenses,
Perspicacité can ride your existing browser session by replaying the
cookies your browser already has — the same trick the Zotero Connector
browser extension uses, just from server-side.

**One-command setup:**

```bash
# 1. Log in to your library proxy / publisher SSO in your browser
#    (Chrome / Brave / Firefox / Edge / Safari / Opera / Arc).
# 2. Install the cookies helper:
uv pip install -e ".[cookies]"

# 3. Export the cookies you care about:
perspicacite import-browser-cookies \
    --browser brave \
    --domain nature.com \
    --domain wiley.com \
    --domain sciencedirect.com \
    --domain pubs.acs.org \
    --output ~/.config/perspicacite/cookies.txt
```

The command reads + decrypts cookies via the OS keychain (macOS may
prompt once), filters to the requested domains, writes a Netscape
`cookies.txt` with `chmod 600`, and prints the matching `config.yml`
block to paste under `pdf_download:`. Typical output:

```text
Wrote 54 of 3122 cookies to /Users/me/.config/perspicacite/cookies.txt
Top cookie hosts captured:
   18  www.nature.com
   12  onlinelibrary.wiley.com
   ...

Add to your config.yml:

pdf_download:
  cookies_path: "/Users/me/.config/perspicacite/cookies.txt"
  cookie_domains:
    - "nature.com"
    - "onlinelibrary.wiley.com"
    - "pubs.acs.org"
    ...
```

After updating `config.yml` and restarting, every PDF request to those
hosts carries your session — paywalled PDFs the institution licenses
become reachable.

**Verifying it works:**

```bash
# Add a paywalled DOI; check pdf_download.success in the response:
curl -s -X POST http://localhost:8000/api/kb/<your-kb>/dois/async \
  -H "Content-Type: application/json" \
  -d '{"dois":["10.1038/s41586-023-06924-6"]}'

curl -sN "http://localhost:8000/api/jobs/<job_id>/events"
# Expect: {"pdf_download":{"attempted":1,"success":1,"failed":0}}
```

**Notes:**
- Empty `cookie_domains` list = attach cookies to **all** PDF requests
  (broader access, slight cookie-leak risk in third-party redirect hops).
- Cookies expire — re-run `import-browser-cookies` when downloads start
  failing again.
- The CLI's auto-suggested `cookie_domains` may include extra subdomains
  (`assets.nature.com`, etc.); trim to what your institution licenses.
- The cookies file is written `chmod 600`; anyone with read access can
  impersonate your library session.

**Manual export still works** — drop a Netscape-format `cookies.txt`
from any "Get cookies.txt" / "EditThisCookie" browser extension at the
configured `cookies_path` and Perspicacité picks it up the same way.

### Use the local Zotero desktop API

If you have Linked Files / ZotFile-managed PDFs or simply don't want to
hit Zotero's rate limits, point at the desktop app's local API:

1. Zotero 7+ → Settings → Advanced → check **"Allow other applications on
   this computer to communicate with Zotero"** (Zotero 6 → about:config →
   `extensions.zotero.httpServer.enabled = true`).
2. Restart Zotero.
3. In `config.yml`:
   ```yaml
   zotero:
     enabled: true
     base_url: "http://localhost:23119/api"
     library_id: "5691738"        # your group/user library ID
     library_type: "group"        # or "user"
     api_key: ""                  # optional on loopback
   ```

The local API is **read-only** (you cannot push items to it — use the
cloud API for `push_to_zotero`).

---

## Knowledge Bases

**Create from BibTeX:**
- Web UI: click "+ Create new KB", drag a `.bib` file, enter a name
- CLI: `perspicacite create-kb my-kb --from-bibtex refs.bib`
- MCP: `create_knowledge_base` then `add_papers_to_kb`

**Add papers during research:**
- Agentic mode finds and downloads papers — click "Add to KB" to save
- Literature Survey mode lets you select recommended papers and add in bulk

**Chunking strategies:**

| Strategy | Description |
|----------|-------------|
| `token` | Fixed-size token chunks (default) |
| `semantic` | Splits at semantic boundaries |
| `agentic` | AI-driven chunking optimized for RAG |

**Multi-KB queries:** Pass `kb_names` (a list of KB names) in the chat advanced options or the `generate_report` MCP tool to fan a query across multiple KBs simultaneously. All queried KBs must share the same embedding model.

**Tips:**
- Keep KBs focused — create separate ones for different projects
- Start with 10–20 key papers and expand as needed
- Pay attention to relevance scores — higher-scoring papers are most useful

---

## Development

### Run Tests

```bash
# Unit tests (no external services needed)
uv run pytest tests/unit/ -v

# Skip tests requiring live API keys
uv run pytest tests/unit/ -m "not live" -v

# Live MCP tests (requires running server on port 8000)
uv run python tests/test_mcp_live.py --all --port 8000
uv run python tests/test_mcp_live.py --test search
```

### Lint and Type Check

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
uv run mypy src/
```

### Project Structure

```
src/perspicacite/
  cli.py                        # CLI commands (serve, create-kb, screen-papers, pubmed-search, version)
  config/schema.py              # Pydantic configuration model
  mcp/server.py                 # MCP server with 18 tools
  pipeline/
    download/                   # Content retrieval pipeline
      discovery.py              # OpenAlex + Unpaywall discovery
      unified.py                # retrieve_paper_content() — main entry point
      europepmc.py              # PMC JATS XML fetcher
      arxiv.py                  # arXiv HTML + PDF
      biorxiv.py                # bioRxiv/medRxiv JATS XML fetcher
      crossref.py               # Crossref metadata enrichment
    parsers/pdf.py              # PyMuPDF-based parser
    bibtex_kb.py                # BibTeX → KB pipeline
  rag/
    engine.py                   # RAGEngine (routes to mode handlers)
    modes/                      # basic, advanced, profound, agentic, literature_survey, contradiction
    tools/                      # Tool registry, KB search, LOTUS
  retrieval/                    # ChromaDB vector store + hybrid BM25 search
    multi_kb.py                 # MultiKBRetriever — fan-out across multiple KB collections
    recency.py                  # apply_recency_weighting() — exponential decay by year
  search/
    scilex_adapter.py           # Multi-database literature search
    screening.py                # screen_papers() BM25 + LLM relevance scoring
    pubmed.py                   # PubMedSearchAdapter (Biopython Entrez)
  web/                          # FastAPI app, routers, AppState singleton
templates/index.html            # Single-page chat UI
static/css/                     # 6 stylesheets: theme, base, layout, chat, kb, survey
static/js/                      # 10 scripts: utils, databases, mode, conversations, chat, kb, kb_stats, paper_detail, survey, main
```

> **Developer note:** After editing files in `static/css/` or `static/js/`, force a browser hard-refresh (Ctrl+Shift+R / Cmd+Shift+R) to bypass the cache. A `MANUAL_QA.md` checklist at the repo root covers the Phase 5 UI features.

---

## Privacy & Data

- **Your data stays local** — KBs are stored in ChromaDB and SQLite on your machine
- **API calls only** — Queries are sent to your configured LLM provider; no data is sent elsewhere
- **No tracking** — No usage analytics are collected

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for guidelines.

---

## License

Apache License 2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

---

## Citation

<p align="center">
<b>Citation:</b> <em>An AI Pipeline for Scientific Literacy and Discovery: a Demonstration of Perspicacité-AI Integration with Knowledge Graphs</em><br>
L. Pradi, T. Jiang, M. Feraud, M. Bekbergenova, Y. Taghzouti, L.-F. Nothias — <em>ISWC-C 2025</em>
</p>

```bibtex
@inproceedings{pradi2025perspicacite,
  title     = {An AI Pipeline for Scientific Literacy and Discovery: a Demonstration of Perspicacit\'{e}-AI Integration with Knowledge Graphs},
  author    = {Pradi, Lucas and Jiang, Tao and Feraud, Matthieu and Bekbergenova, Madina and Taghzouti, Yousouf and Nothias, Louis-Felix},
  booktitle = {ISWC-C 2025},
  year      = {2025}
}
```

```bibtex
@softwareversion{scilex2026,
  title  = {SciLEx, Science Literature Exploration Toolkit},
  author = {Ringwald, C\'{e}lian and Navet, Benjamin},
  url    = {https://github.com/Wimmics/SciLEx},
  year   = {2026}
}
```

---

## Acknowledgments

- **[SciLEx](https://github.com/Wimmics/SciLEx)** — literature exploration toolkit powering multi-database search
- **[ChromaDB](https://www.trychroma.com/)** — local vector storage
- **[OpenAlex](https://openalex.org/)** and **[Semantic Scholar](https://www.semanticscholar.org/)** — academic search
- **[Unpaywall](https://unpaywall.org/)** — open access discovery
