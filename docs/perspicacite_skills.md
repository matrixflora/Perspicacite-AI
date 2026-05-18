# Perspicacité MCP Skills Reference

This file guides external agents (Mimosa-AI and similar) in using Perspicacité's
11 MCP tools effectively. It covers prerequisites, recommended workflows, and
failure recovery patterns.

## Prerequisites

Before calling tools, confirm the Perspicacité MCP server is running and
initialized. Call the `perspicacite://info` resource to verify status.

### Environment Variables

| Variable | Required For | Purpose |
|----------|-------------|---------|
| `UNPAYWALL_EMAIL` | `get_paper_content` | Unpaywall OA discovery — must be a real email, `@example.com` addresses are rejected |
| `OPENALEX_MAILTO` | `search_literature`, `get_paper_content` | OpenAlex polite pool (faster) |
| `SEMANTIC_SCHOLAR_API_KEY` | `search_literature` | Semantic Scholar (higher rate limits) |
| `SPRINGER_API_KEY` | `get_paper_content` | Springer PDF download |
| `ELSEVIER_API_KEY` | `get_paper_content` | Elsevier full text |
| `PUBMED_API_KEY` | `search_literature` | PubMed search |
| `ZOTERO_API_KEY` | `push_to_zotero` | Zotero Web API key |

If keys are missing, tools still work but with lower rate limits or reduced
content coverage.

### Content Availability

Not every paper has accessible full text. The priority pipeline is:

1. **PMC JATS XML** — structured sections + references (best quality)
2. **Europe PMC** — structured full text for Europe PMC Open Access papers
3. **arXiv HTML** — structured sections
4. **Publisher PDF** — parsed to plain text
4. **Abstract only** — from OpenAlex metadata
5. **None** — paper exists but no content retrievable

Check `content_type` in the response: `"structured"` means full text with
sections, `"full_text"` means parsed PDF, `"abstract"` means abstract-only,
`"none"` means nothing available.

---

## Tool Reference

### search_literature

```
search_literature(query, max_results=20, year_min=None, year_max=None,
                  article_type=None, databases=None)
```

Searches academic databases. Returns a JSON list of papers with title, authors,
year, DOI, abstract, journal, citation count, and URL.

**Notes:**
- Default year range is `[current_year - 3, current_year]` if not specified.
- `article_type` accepts `"review"`, `"article"`, `"conference"`, `"preprint"`.
  Review filtering uses keyword heuristics (title/journal contains "review",
  "survey", "systematic review").
- `databases` options: `semantic_scholar`, `openalex`, `pubmed`, `arxiv`.
  Default is `["semantic_scholar", "openalex", "pubmed"]`.

### get_paper_content

```
get_paper_content(doi, include_sections=True)
```

Fetches full text for a single paper. Returns content type, source, text length,
sections dict, and references.

**Notes:**
- DOI should be bare (e.g., `"10.1038/s41586-024-12345-6"`), but
  `https://doi.org/` prefixed DOIs are also accepted.
- `include_sections=False` skips section extraction (faster for plain text).
- This can be slow (5-30s) if content isn't cached — it tries multiple sources.

### get_paper_references

```
get_paper_references(doi)
```

Returns cited references from a paper. Works best for PMC Open Access papers
with JATS XML. Falls back to running the full content pipeline to populate the
reference cache.

**Notes:**
- Only PMC-sourced papers reliably return structured references.
- Returns a list of dicts with `doi`, `title`, `authors`, `year` when available.
- Some references may have only `text` (raw citation string) if structured
  data is unavailable.

### list_knowledge_bases

```
list_knowledge_bases()
```

Returns all KBs with name, description, paper_count, chunk_count, created_at.

### create_knowledge_base

```
create_knowledge_base(name, description="")
```

Creates an empty KB. Name must be alphanumeric with hyphens/underscores.

### add_papers_to_kb

```
add_papers_to_kb(kb_name, papers)
```

Adds papers to a KB. Each paper dict should have `title` and optionally `doi`,
`year`, `authors`, `abstract`, `citations`. Automatically downloads and indexes
full text for papers with DOIs.

**Notes:**
- Returns `added_papers`, `added_chunks`, and `pdf_stats` (attempted/success/
  failed).
- Papers without DOIs are added with abstract-only if available.
- This is the slowest tool — each paper may take 5-30s for content retrieval.

### search_knowledge_base

```
search_knowledge_base(query, kb_name="default", top_k=5)
```

Semantic search within a KB. Returns matching chunks with paper title, section,
text, relevance score, and DOI.

### generate_report

```
generate_report(query, kb_name="default", mode="advanced", max_papers=10,
                recency_weight=0.0, kb_names=None)
```

Generates a synthesized research report from a KB using RAG.

**Modes:**
- `"basic"` — fast single-pass, no web search, KB content only
- `"advanced"` — query expansion, re-ranking (recommended default)
- `"profound"` — multi-cycle deep analysis (slowest, best quality)
- `"contradiction"` — clusters claims across papers into agreement / disagreement / open-question buckets; degrades gracefully when <3 papers are retrieved

**Args:**
- `recency_weight` (float 0..1, default 0.0) — apply exponential recency decay to retrieved chunks; 0 disables it.
- `kb_names` (list of str, optional) — fan the query across multiple KBs simultaneously (all must share the same embedding model).

**Notes:**
- The KB must contain relevant papers before generating a report.
- Returns report text, cited sources, and metadata.

### screen_papers

```
screen_papers(candidates, query, method="bm25", threshold=0.3, max_results=50)
```

Scores candidate papers (DOIs or titles) by relevance to a research query without
adding them to a KB.

**Args:**
- `candidates` — list of DOIs (e.g. `"10.1234/abc"`) or plain-text titles.
- `query` — research topic/question to screen against.
- `method` — `"bm25"` (fast, no LLM) or `"llm"` (LLM-rated 0-1 with reasons).
- `threshold` — keep papers scoring >= this value (0–1).
- `max_results` — cap on returned items.

**Notes:**
- DOI candidates trigger abstract retrieval via the unified pipeline.
- BM25 mode requires no API calls beyond optional DOI abstract lookup.
- Returns `screened` list with `score`, `kept`, `reason`, and `doi`/`title`.

### add_dois_to_kb

```
add_dois_to_kb(kb_name, dois)
```

Bulk-adds papers to a knowledge base directly from a list of DOIs (max 200 per
call). For each DOI the tool fetches full text via the unified download pipeline,
deduplicates against existing KB content, and indexes the result.

**Args:**
- `kb_name` — target KB name (must exist).
- `dois` — list of DOI strings (bare or `https://doi.org/`-prefixed).

**Notes:**
- Returns `added_papers`, `added_chunks`, `skipped_duplicates`, `failed` (list),
  and `pdf_download` stats (attempted/success/failed).
- Papers already present in the KB are silently skipped (deduplicated by DOI).
- Content retrieval follows the same priority pipeline as `get_paper_content`.
- Use this tool when you have a list of DOIs from an external source (e.g.
  citation export, `screen_papers` output) and want to ingest them directly.

### push_to_zotero

```
push_to_zotero(dois)
```

Pushes one or more papers to the configured Zotero library. Accepts a single DOI
string or a list of DOI strings.

**Args:**
- `dois` — a DOI string (e.g. `"10.1038/s41586-024-12345-6"`) or a list of DOI
  strings (bare or `https://doi.org/`-prefixed).

**Returns:** JSON object with three lists:
- `created` — DOIs successfully added to Zotero.
- `skipped` — DOIs already present in the Zotero library.
- `failed` — DOIs that could not be pushed (network error, metadata missing, etc.).

**Errors:**
- Returns `{"error": "zotero_not_configured"}` when Zotero integration is disabled
  or the required config values are absent.

**Required config** (`config.yml`):

```yaml
zotero:
  enabled: true
  api_key: "your_zotero_web_api_key"
  library_id: "1234567"          # numeric user or group library ID
  library_type: "user"           # "user" (default) or "group"
  collection_key: "ABCD1234"     # optional — push into a specific collection
```

**Example call:**

```python
# Push a single DOI
result = await client.call_tool("push_to_zotero", {"dois": "10.1038/s41586-024-12345-6"})

# Push multiple DOIs
result = await client.call_tool("push_to_zotero", {
    "dois": ["10.1038/s41586-024-12345-6", "10.1126/science.abc1234"]
})
# → {"created": ["10.1038/..."], "skipped": [], "failed": []}
```

### web_search

```
web_search(query, databases=None, max_results=10, enrich=True, optimize_query=True)
```

Live academic web search across user-selected databases. Returns cleaned metadata
with optional Crossref enrichment. Distinct from `search_literature` (SciLEx-only)
and `generate_report` (full RAG). Use this for focused literature lookups without
spinning up a full RAG pipeline.

**Args:**
- `query` — free-text scientific query.
- `databases` — list of provider names. Defaults to `["semantic_scholar", "openalex", "pubmed"]`.
  Additional providers: `google_scholar`, `europepmc`, `core`, `inspire`, `pubchem`.
- `max_results` — cap on returned papers (1–50, default 10).
- `enrich` — when `True` (default), runs Crossref enrichment to fill missing abstracts
  and canonicalise author lists.
- `optimize_query` — when `True` (default), runs LLM-assisted keyword rewrite before
  searching.

**Returns:** JSON string with:
- `papers` — list of paper objects (title, authors, year, journal, doi, url, abstract,
  discovery_sources, enrichment_sources).
- `warnings` — list of provider-level warning strings (currently empty; surfaced in logs).
- `telemetry_summary` — `{"by_provider": {"semantic_scholar": N, ...}}` hit counts.
- `error` — only present on failure; `papers` will be `[]`.

**Example call:**

```python
result = await client.call_tool("web_search", {
    "query": "CRISPR base editing off-target effects",
    "databases": ["semantic_scholar", "europepmc"],
    "max_results": 20,
})
# → {"papers": [...], "warnings": [], "telemetry_summary": {"by_provider": {...}}}
```

---

## Recommended Workflows

### Literature Survey

For answering a research question from scratch:

```
1. search_literature(query="machine learning drug discovery",
                     max_results=20,
                     article_type="review")
   → Get list of review papers

2. get_paper_content(doi="10.1234/best_review")
   → Verify the most relevant paper has full text

3. create_knowledge_base(name="ml-drug-discovery",
                         description="ML for drug discovery reviews")

4. add_papers_to_kb(kb_name="ml-drug-discovery",
                    papers=[{top 5-10 papers from step 1}])
   → Wait for indexing (slow step)

5. generate_report(query="What are the current ML approaches for drug discovery?",
                   kb_name="ml-drug-discovery",
                   mode="advanced")
   → Get synthesized report
```

### Single Paper Analysis

For deep-diving into a specific paper:

```
1. get_paper_content(doi="10.1234/paper")
   → Get full text + sections

2. get_paper_references(doi="10.1234/paper")
   → Get citation list

3. For key references, call get_paper_content on each
   → Build context around the paper
```

### Knowledge Base Exploration

For querying an existing KB:

```
1. list_knowledge_bases()
   → See what's available

2. search_knowledge_base(query="transformer attention mechanism",
                         kb_name="existing-kb",
                         top_k=10)
   → Find relevant chunks

3. generate_report(query="How do transformer attention mechanisms work?",
                   kb_name="existing-kb")
   → Get synthesized answer
```

---

## Failure Recovery

### Empty search results

If `search_literature` returns `[]`:
- Broaden the query (use fewer/more general keywords)
- Expand year range (default is only last 3 years)
- Try different databases: `["openalex", "semantic_scholar"]`
- Remove `article_type` filter

### Paper content unavailable

If `get_paper_content` returns `content_type: "none"`:
- The paper may not be Open Access and no abstract is indexed in OpenAlex
- Try `search_literature` with the paper title to get the abstract
- Check if an arXiv preprint version exists (search by title)

### PDF download failures

If `add_papers_to_kb` reports many `pdf_stats.failed`:
- Content retrieval depends on OA availability — this is expected
- Papers still get added with abstracts if available
- Re-run with specific DOIs that have confirmed OA status

### KB not found

If `search_knowledge_base` or `generate_report` returns a KB not found error:
- Call `list_knowledge_bases()` to verify the exact name
- Create the KB with `create_knowledge_base` if needed
- Add papers before generating reports (basic mode requires KB content)

### Slow responses

- `get_paper_content` can take 5-30s per paper (network fetches)
- `add_papers_to_kb` is the slowest tool — batch papers carefully
- `generate_report` in `"profound"` mode may take 60s+
- Use `"basic"` mode for quick answers, `"advanced"` for balanced quality

### Rate limiting

- OpenAlex: 10 req/s without email, 100 req/s with `OPENALEX_MAILTO`
- Semantic Scholar: 100 req/5min without key, higher with key
- Unpaywall: 100k req/day per email
- PMC S3: no rate limit, but cache results via `add_papers_to_kb`
