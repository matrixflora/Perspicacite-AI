# Perspicacité — Framework Vision and Core Capabilities

*Version 2.0.0 — 2026-05-15*

---

## 1. The Problem

Academic research has a retrieval problem that commercial AI tools have not solved —
they have mostly obscured it.

When a researcher asks a general-purpose LLM about a field, the answer is plausible
but unverifiable. Which papers were retrieved? What database were they from? Was the
claim in the abstract or the methods section? The model cannot say, because it has no
retrieval trace to expose. The result is confident-sounding text that mixes genuine
findings with confabulations, with no practical way to distinguish them.

The structural pain points are:

- **Paywall opacity.** The open-access movement has not yet won. A large fraction of
  the most-cited literature is behind publisher paywalls. Researchers at institutions
  without broad licensing agreements — or outside academia entirely — hit this wall
  daily. Tools that call the web do not help: they hit the same paywalls, or scrape
  abstracts and pretend they have full text.

- **Provenance erasure.** Commercial "research AI" products routinely paper over their
  retrieval layer. A user asking about a specific drug mechanism has no way to know
  whether the answer came from a 2024 RCT or a 2009 review or a preprint that was
  later retracted. This is not a minor inconvenience — it is an epistemological hazard.

- **Reproducibility of literature surveys.** A human doing a systematic review must
  document every database queried, every filter applied, every inclusion/exclusion
  decision. There is no standard way to do that in AI-assisted search. Survey results
  are not reproducible across sessions or between researchers.

- **Workflow fragmentation.** Researchers already live in Zotero and Obsidian. They
  want AI assistance that plugs into those workflows, not a parallel silo that requires
  re-entering bibliographic metadata or disconnects from their citation manager.

- **Citation-graph blind spots.** OpenAlex — the primary open academic graph —
  systematically underreports citations for arXiv preprints. A paper circulated as a
  preprint before journal publication is often indexed under multiple IDs; forward
  citations may land only on the DOI version, not the arXiv version. A tool that only
  walks OpenAlex will miss a substantial fraction of the intellectual lineage for
  preprint-heavy fields like machine learning, physics, and computational biology.

Perspicacité is built to address these problems directly, without pretending they do
not exist.

---

## 2. Design Philosophy

Five principles constrain every architectural decision:

**Local-first.** Your knowledge bases, your PDFs, your conversation history, your
provenance traces — all of it lives on your machine. The only data that leaves your
environment are LLM inference payloads (sent to your configured provider) and
academic-API queries (to Semantic Scholar, OpenAlex, PubMed, arXiv, etc.). You choose
the LLM provider; you hold the API keys; you control the data path. There is no
Perspicacité cloud service to trust.

**Transparent provenance.** Every answer carries the retrieval trace that produced it:
which chunks were retrieved, from which papers, via which KB, using which RAG mode and
model, at what latency. This trace is stored in SQLite and is exportable as an
RO-Crate 1.1 zip bundle — the Research Object format used by reproducible science
workflows. The goal is that a literature survey produced by Perspicacité could, in
principle, satisfy the documentation requirements of a systematic review protocol.

**Composable surfaces.** The CLI, the REST API, and the MCP server all surface the
same underlying primitives. A pipeline you build with the CLI can be replicated via
the REST API and automated via an MCP-connected agent without rewriting business logic.
Adding a new capability — say, a new content-fetch strategy — appears automatically
in all three surfaces because they share the same engine layer.

**Honest sourcing.** `Paper.source` carries a `PaperSource` enum value that records
where the paper actually came from: `OPENALEX`, `PUBMED`, `ARXIV`, `CROSSREF`,
`SEMANTIC_SCHOLAR`, `BIBTEX`, `LOCAL`, etc. There is no generic `WEB_SEARCH` catch-all
that blurs the distinction between a PubMed hit and a Semantic Scholar hit. When you
query a knowledge base, the provenance trace tells you not just which papers were
retrieved but which database each paper originated from. This matters for
reproducibility: a Semantic Scholar ID and an OpenAlex ID for the same paper may carry
different citation counts, different co-author lists, different DOI resolution paths.

**Reproducible walks.** The citation-graph snowball algorithm is deterministic given
the same seed set and parameters. Forward and backward expansion over OpenAlex, with
an automatic Semantic Scholar fallback for arXiv-seeded papers, produces the same
output on the same data. Filters (year floor, citation threshold, venue denylist) are
recorded with the expansion run. An `expand-kb` run is, in principle, auditable.

---

## 3. Architecture Overview

Perspicacité is organized in five layers, each independently testable and replaceable.

### 3.1 Ingestion layer

**Multi-database search.** Literature discovery is delegated to SciLEx — a
MIT-licensed multi-database aggregator that fans queries across Semantic Scholar,
OpenAlex, PubMed, arXiv, HAL, DBLP, and IEEE. A single `search-to-kb` call fires
parallel queries across all configured databases, merges and deduplicates results by
DOI, and hands the unified hit list to the filter and screen stage. Optional LLM
rephrasing (`--rephrase N`) generates N alternate query phrasings and fans each out
separately, increasing recall for keyword-sensitive databases.

**Unified content pipeline.** Every paper goes through a quality-priority content
routing chain:

```
1. Discovery      — OpenAlex + Unpaywall → PMCID, arXiv ID, OA status, abstract
2. Structured     — PMC JATS XML → Europe PMC → arXiv HTML (sections + references)
3. PDF full text  — OA PDF, arXiv PDF, Unpaywall, publisher APIs (ACS, Springer,
                     Wiley, Elsevier, …), or browser-cookie-gated institutional access
4. Abstract only  — from discovery metadata when no full text is available
5. Discard        — returns failure for papers with no retrievable content
```

Structured content (PMC JATS, arXiv HTML) yields sections and references. PDF content
is parsed via PyMuPDF. Papers behind paywalls with no OA path are served as abstracts
and flagged `content_type: "abstract"` in metadata. Institutional-access PDFs are
reachable by replaying browser cookies — the same mechanism the Zotero Connector uses.

**Citation-graph expansion.** The snowball walker grows a KB by following one citation
hop from each of its seed papers. OpenAlex is the primary graph; for papers that were
seeded via arXiv IDs (common in ML, physics, CS), a Semantic Scholar fallback fires
automatically because OpenAlex underreports preprint citations. The two hit streams
are merged and deduplicated by DOI before the filter + screen stage. In practice, for
a representative RAG paper that OpenAlex returned 18 forward citations for, the
combined OpenAlex + SS path returned 43 — a 2.4× increase.

### 3.2 Knowledge storage

Knowledge bases are stored in three co-located layers:

- **SQLite** — paper metadata, conversation history, provenance records, async job
  state, and the FTS5 full-text search index over conversations.
- **ChromaDB** — vector embeddings, chunked from full text and indexed by KB. The
  default embedding model is `text-embedding-3-small`; the config allows per-KB
  overrides. A KB is permanently bound to the embedding model that created it.
- **Disk** — cached PDFs (keyed by DOI, with `.meta.json` sidecar recording source,
  fetch timestamp, size, sha256), and capsule artifacts (figures, referenced code
  files, supplementary files) organized by paper ID under `data/capsules/`.

### 3.3 Retrieval layer

Within a KB, retrieval is hybrid: BM25 lexical scoring (via `bm25s`) is combined with
vector cosine similarity (via ChromaDB) using a configurable weighting. Two-pass
retrieval (`use_two_pass: true`) fetches a broad first pass and a focused second pass
to improve full-paper context coverage. Multi-KB routing (`kb_name: "auto"`) scores
every KB's description and sampled titles against the query (BM25 or one cheap LLM
call) and fans the query across the top-N matching KBs in parallel.

Contextual retrieval tiers control how much context is prepended to each embedded
chunk, from `"none"` (structural prefix only, free) through `"abstract"` (paper
abstract prepended to every chunk, zero LLM calls) to `"chunk"` (one LLM call per
chunk, closest to the Anthropic benchmark that showed 30-40% recall improvement on
technical content).

### 3.4 Reasoning layer

Six RAG modes expose different cost/quality/depth trade-offs:

| Mode | Strategy | Cost |
|------|----------|------|
| Basic | Single hybrid retrieval pass | Free (no LLM synthesis if desired) |
| Advanced | Query expansion + WRRF fusion + reranking | One synthesis call |
| Profound | Multi-cycle (up to 3 iterations) with self-evaluation | 3× synthesis calls |
| Agentic | Intent-based agent with tool use, up to 5 iterations | Variable (5 LLM budget) |
| Literature Survey | Broad search, theme clustering, recommendations | Highest; can checkpoint/resume |
| Contradiction | Multi-paper claim clustering into agreement / disagreement / open | Medium |

Per-stage model tiering allows routing cheap decisions (query relevance screening,
KB routing, query rephrasing, contextual prefix generation) to Haiku or a local Ollama
model, while synthesis uses Sonnet or Opus. Budget caps and checkpoint/resume (for
Literature Survey mode) prevent runaway costs on long queries.

### 3.5 Surface layer

All capabilities are exposed through three co-equal interfaces:

- **CLI** — `perspicacite <subcommand>` for interactive and scripted use. Structured
  JSON logs go to stderr; clean output goes to stdout so results can be piped into
  `jq` or `tee`.
- **REST API** — FastAPI application with SSE streaming for long-running operations
  (async BibTeX import, DOI bulk-add, literature survey). The web UI at `:8000` is
  a single-page app backed by this API.
- **MCP server** — 23 tools (as of 2026-05-15) at `/mcp`, using the streamable-HTTP
  transport. Clients include Mimosa-AI, SmolAgents, Claude Code, and any
  MCP-compatible agent framework.

---

## 4. Core Capabilities

### Search and discover

- **Multi-database fan-out** via SciLEx: Semantic Scholar, OpenAlex, PubMed, arXiv,
  HAL, DBLP — one query, unified deduped results.
- **KB-aware query expansion** (`--kb-aware`): when a target KB already exists,
  Perspicacité mixes in topic terms from the KB's description and sampled titles to
  bias discovery toward adjacent literature.
- **LLM/BM25 relevance screen** (`--screen llm|bm25`): between search and ingest, a
  cheap screen pass scores each candidate paper's relevance to the query. BM25 is
  free; LLM (Haiku-grade) costs fractions of a cent per paper.
- **Multi-variant rephrasing** (`--rephrase N`): one LLM call generates N alternate
  phrasings, each fanned out across SciLEx. Results are merged and deduplicated.

### Build and maintain knowledge bases

- **BibTeX import** — drag a `.bib` into the UI or `perspicacite create-kb --from-bibtex`.
  Full-text download, chunk, embed, index — one shot.
- **DOI bulk-add** — REST `POST /api/kb/{name}/dois` or MCP `add_dois_to_kb` (max 200
  DOIs per call). Async variant with SSE progress streaming.
- **Citation-graph snowball** — `perspicacite expand-kb` / MCP `expand_kb_via_citations`.
  Forward and backward citation hops over OpenAlex, with automatic Semantic Scholar
  fallback for arXiv-seeded papers. Same filter + screen pipeline as `search-to-kb`.
- **Local document ingest** — `perspicacite ingest-local` / MCP `ingest_local_documents`.
  PDFs, Markdown, code files from configured allowlist roots.
- **Zotero-from-local-API ingest** — build one KB per Zotero top-level collection
  via MCP `build_kbs_from_zotero` or the REST `POST /api/zotero-ingest/build-kbs/async`.

### Reason over evidence

- **Six RAG modes** — see Section 3.4 above.
- **Capsule-aware retrieval** — when capsules are built (`build-capsule` /
  `build_capsules_for_kb`), chunk embeddings include figure captions, referenced code
  snippets, and supplementary file content alongside the main text. Answers can cite a
  specific figure or a GitHub script, not just a paragraph.
- **Multi-KB routing** — `kb_name: "auto"` or `kb_names: [...]` fans the query across
  multiple knowledge bases simultaneously and merges ranked results.
- **Provenance per answer** — every RAG response carries a trace: retrieved chunk IDs,
  paper IDs, KB name, mode, model, latency in milliseconds. Accessible via REST
  `GET /api/conversations/{id}/provenance` or in the RO-Crate export.

### Preserve and share

- **PDF byte cache** — `pdf_download.cache_pdfs: true` (default). Every fetched PDF
  lands in `data/papers/` keyed by DOI. Re-ingest is served from disk (100-200× faster
  than re-fetching). Sidecar `.meta.json` records provenance.
- **Zotero attachment push** — MCP `push_to_zotero(attach_pdf=True,
  attach_supplementary=True)` uses Zotero's 4-step file-upload Web API to attach the
  cached PDF and capsule supplementary files to the Zotero item.
- **Obsidian vault export** — REST `GET /api/kb/{name}/export?format=obsidian-vault`
  returns a zip of Markdown files, one per paper, with YAML frontmatter suitable for
  Obsidian.
- **BibTeX + folder export** — `perspicacite export-kb --with-pdfs` writes a `.bib`
  with BetterBibTeX `file` fields and copies cached PDFs alongside. Drag the `.bib`
  into Zotero to auto-attach PDFs via the `file` field.

### Integrate

- **MCP server** (23 tools) — native integration for Mimosa-AI, Claude Code, Codex,
  SmolAgents, and any future MCP-compatible agent. Runs at `http://localhost:8000/mcp`
  by default using the streamable-HTTP transport. See
  [`docs/reference/mcp-tools.md`](reference/mcp-tools.md) for the full tool catalog.
- **REST API** — JSON API with SSE streaming for async jobs. See
  [`docs/reference/rest-api.md`](reference/rest-api.md).
- **CLI** — all subcommands available for scripted pipelines. See
  [`docs/reference/cli.md`](reference/cli.md).

---

## 5. What Is Deliberately Not Here

**Not a general-purpose chat tool.** Perspicacité is scoped to academic literature.
It has no web-browsing mode, no code execution, no image generation. Every RAG mode
grounds answers in retrieved paper chunks. If you want a general assistant, you already
have one; Perspicacité plugs into it as an MCP server.

**Not an opinionated paper recommender.** Perspicacité does not maintain a centralized
relevance model or rank papers by predicted user interest. Relevance scoring is
performed locally against your query using BM25 and cosine similarity. If you want
recommendations, `screen-papers` will score a candidate set against your topic — but
the ranking is transparent and the logic is yours to inspect.

**Not a hosted SaaS.** Local-first is intentional, not a limitation. Running
Perspicacité means owning the full stack: your keys, your data, your LLM provider
choice. There is no cloud offering and no telemetry. This is a trade-off: setup takes
five minutes and requires a machine with disk space, but you are not subject to
a vendor's retention policy or rate limits.

**Not a replacement for Zotero or Mendeley.** Perspicacité integrates with Zotero —
pushing papers to your library, attaching PDFs, building KBs from collections.
It deliberately avoids duplicating Zotero's UI, cloud sync, or citation-key management.
Interop is always preferred over duplication.

---

## 6. Roadmap Pointers

Active design work lives in [`docs/superpowers/plans/`](superpowers/plans/) —
these are implementation plans, each tied to a specific feature and tracked through
execution. Accepted-but-not-yet-implemented designs live in
[`docs/superpowers/specs/`](superpowers/specs/).

**Recent landmarks (2026-05-15):**

- **`PaperSource` enum migration** — every `Paper` construction site in the codebase
  now stamps the true origin database: `OPENALEX`, `PUBMED`, `ARXIV`, `CROSSREF`,
  `SEMANTIC_SCHOLAR` alongside the legacy values (`BIBTEX`, `LOCAL`,
  `CITATION_FOLLOW`). The generic `WEB_SEARCH` value is kept for backward
  compatibility but is no longer the default for any ingestion path. See
  [`docs/reference/paper-source-enum.md`](reference/paper-source-enum.md).

- **Semantic Scholar fallback cite-graph** — the snowball walker now auto-detects
  arXiv-seeded papers and fires a parallel SS forward+backward walk. The two streams
  are merged and deduplicated before the filter stage. For the representative RAG
  paper tested (arXiv:2005.11401), OpenAlex returned 18 forward citations;
  the combined path returned 43. See
  [`docs/concepts/citation-graph.md`](concepts/citation-graph.md) for the full design.

Planned work visible in `docs/superpowers/plans/` as of this writing includes:
budget caps and checkpoint/resume for long synthesis runs; multimodal capsule
extraction (figure captioning, table parsing); embedding cache to avoid re-computing
embeddings for unchanged chunks; versioned KBs; and ORCID-based author disambiguation.
None of these are promised delivery dates — they are tracked design intentions.
