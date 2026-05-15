# Knowledge Bases

A knowledge base (KB) is the primary organizational unit in Perspicacité. It is a
named, indexed collection of academic papers that you can search and query with
natural language. This document explains what a KB is structurally, how to create and
manage one, and how multi-KB routing works.

---

## What a KB is

Each KB has three co-located storage layers:

1. **SQLite metadata** — paper records (title, authors, year, DOI, abstract, source
   database, content type), KB metadata (name, description, creation time, embedding
   model), and chunk-level records linking paper IDs to Chroma chunk IDs.
2. **ChromaDB collection** — vector embeddings for every chunk produced from the
   papers' full text. The collection name is derived from the KB name
   (`perspicacite_<sanitized_name>`). A KB is permanently bound to the embedding
   model that created it — you cannot switch embedding models mid-KB.
3. **Disk artifacts** — cached PDFs under `data/papers/` (keyed by DOI, with a
   `.meta.json` sidecar), and capsule artifacts (figures, code snippets, supplementary
   files) under `data/capsules/<paper_id>/` when capsules have been built.

A KB does not store the raw BibTeX or the original PDF in a retrievable form — it
stores what was produced by the unified content pipeline: structured text chunks,
embeddings, and metadata. The raw PDF is cached separately and can be exported.

---

## Creating a KB

### From BibTeX

```bash
perspicacite -c config.yml create-kb my-kb \
  --from-bibtex refs.bib \
  --description "Papers on diamond magnetometry"
```

This runs the full ingestion pipeline: parse BibTeX → discover full text (PMC JATS,
arXiv HTML, OA PDF, publisher APIs) → chunk → embed → index. PDF download is
attempted for every paper that has a DOI; papers without a retrievable full text are
stored as abstracts (`content_type: "abstract"`).

### Empty KB, add papers later

```bash
perspicacite -c config.yml create-kb my-kb --description "My research area"
```

Then add papers by DOI via REST API or MCP:

```bash
# Synchronous (small sets, < 10 DOIs)
curl -X POST http://localhost:5468/api/kb/my-kb/dois \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/s41586-023-06924-6"]}'

# Asynchronous with SSE progress stream (recommended for > 10 DOIs)
curl -X POST http://localhost:5468/api/kb/my-kb/dois/async \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/s41586-023-06924-6", "10.1103/PhysRevLett.131.013001"]}'
```

### From a literature search

Use `search-to-kb` to build a KB from scratch without a pre-existing `.bib`:

```bash
perspicacite -c config.yml search-to-kb \
  --query "nitrogen vacancy diamond magnetometry" \
  --kb diamond_sensors \
  --max-results 30 \
  --min-year 2020
```

See [guides/search-to-kb.md](../guides/search-to-kb.md) for the full workflow.

---

## Listing and inspecting KBs

```bash
# Plain list
perspicacite list-kb

# Machine-readable JSON
perspicacite list-kb --json | jq '.[] | {name, paper_count, embedding_model}'

# REST: KB stats (paper count, chunk count, year histogram, source breakdown)
curl http://localhost:5468/api/kb/my-kb/stats
```

---

## Chunking strategies

| Strategy | Config value | Description |
|----------|-------------|-------------|
| Token | `token` | Fixed-size token chunks (default, `chunk_size: 1000`, `chunk_overlap: 200`) |
| Semantic | `semantic` | Splits at sentence/paragraph boundaries |
| Agentic | `agentic` | AI-driven chunking, highest quality but expensive |

Heading-aware chunking for Markdown files and language-aware chunking for code files
are enabled by default (`markdown_heading_aware: true`, `code_language_aware: true`).

Set per-KB chunking in `config.yml` under `knowledge_base:`.

---

## Contextual retrieval tiers

Contextual retrieval prepends context to each chunk before embedding, improving
retrieval recall at the cost of additional compute:

| Tier | LLM calls | Description |
|------|-----------|-------------|
| `none` | 0 | Structural prefix only (title + section heading). Default. |
| `abstract` | 0 | Prepend paper abstract to every chunk of that paper. |
| `summary` | 1 per paper | LLM-generated 50-100 word paper summary, cached and applied to all chunks. |
| `chunk` | 1 per chunk | Anthropic-style: one LLM call per chunk, most expensive, ~30-40% recall lift. |

Configure with `knowledge_base.contextual_retrieval_tier` in `config.yml`.

---

## Multi-KB routing

When you don't know which KB to query — or want to search across multiple KBs
simultaneously — use auto-routing:

```bash
# REST: auto-route to the best KB(s)
curl -sN -X POST http://localhost:5468/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "metabolomics annotation", "kb_name": "auto", "mode": "basic", "stream": true}'
```

The router scores every KB's description and sampled paper titles against the query
using BM25 (free, fast) or one cheap LLM call. The response includes a `kb_route`
SSE event showing which KBs were selected and their scores.

To query a fixed list of KBs simultaneously:

```bash
curl -sN -X POST http://localhost:5468/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "...", "kb_names": ["sensors_2024", "sensors_2023"], "mode": "basic", "stream": true}'
```

Multi-KB queries require all listed KBs to share the same embedding model.

### Tuning the router

```yaml
# config.yml
rag_modes:
  route_method: "bm25"   # bm25 = free + fast; llm = one cheap LLM call
  route_top_k: 3         # query the top-3 matching KBs
  route_threshold: 0.1   # minimum score to include a KB
```

Use the MCP `route_kbs` tool for introspection without running synthesis — it returns
the scored KB list without generating an answer.

---

## Deleting a KB

```bash
perspicacite delete-kb my-kb
# or
curl -X DELETE http://localhost:5468/api/kb/my-kb
```

Deletion is permanent: the SQLite metadata row and the Chroma collection are both
removed. Cached PDFs under `data/papers/` are not deleted.

---

## Related topics

- [concepts/rag-modes.md](rag-modes.md) — how queries are answered once a KB is selected
- [concepts/capsules.md](capsules.md) — optional per-paper enrichment built on top of KBs
- [guides/ingest-bibtex.md](../guides/ingest-bibtex.md) — step-by-step BibTeX import
- [guides/search-to-kb.md](../guides/search-to-kb.md) — build a KB from a literature search
- [reference/config.md](../reference/config.md) — all `knowledge_base.*` config keys
