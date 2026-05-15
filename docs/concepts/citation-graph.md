# Citation Graph

Perspicacité can grow a knowledge base by following citation links from its existing
papers — a technique called a **snowball walk**. This document explains how the walk
works, why Semantic Scholar is used as a fallback for arXiv-seeded papers, and how to
control the behavior.

---

## How the snowball walk works

A snowball walk starts from a set of **seed papers** — the papers already in the KB —
and follows citation edges one hop in either direction:

- **Forward** (citing) — papers that cite the seed paper, i.e., newer work that built
  on it
- **Backward** (cited) — papers that the seed paper cites, i.e., the intellectual
  lineage it draws from
- **Both** — follow both directions simultaneously

For each direction, Perspicacité calls the OpenAlex citations/references API to
retrieve the neighbor list. The hits are merged, deduplicated by DOI, filtered
(year floor, citation threshold, venue denylist), and optionally screened for topic
relevance before ingest.

The walk is one-hop only: it does not recursively follow the neighbors' neighbors.
This keeps the expansion bounded and the results focused on papers directly connected
to your existing collection.

---

## The arXiv / OpenAlex citation gap

OpenAlex maintains a high-quality academic graph, but it has a systematic blind spot
for arXiv preprints: a paper that first appeared as `arXiv:2005.11401` and was later
published as `10.1038/s42256-020-00278-8` is indexed in OpenAlex under the DOI, not
the arXiv ID. Forward citations in the literature often land on whichever version the
citing author used — in machine learning, physics, and computational biology, many
authors cite the arXiv version rather than the journal DOI. Those citations never
appear in OpenAlex's forward-citation list for the DOI.

In practice this means OpenAlex underreports forward citations for preprint-heavy
papers by a factor of 2-3× compared to what is actually in the literature.

---

## Semantic Scholar fallback

To address this gap, Perspicacité auto-triggers a Semantic Scholar (SS) forward+backward
walk alongside the OpenAlex walk when it detects that a seed paper has an arXiv ID.
Detection works as follows:

1. For each seed paper in the KB, check if its DOI, `pmid`, or metadata contains an
   arXiv identifier (matching `arxiv:`, `ar5iv.org/abs/`, or `arxiv.org/abs/` patterns).
2. If an arXiv ID is found, also fire `GET /graph/v1/paper/arXiv:{id}/citations` and
   `GET /graph/v1/paper/arXiv:{id}/references` against the SS API.
3. Merge the SS hits with the OpenAlex hits, deduplicating by DOI.
4. Run the filter and screen stage over the combined set.

**Measured impact.** For a representative RAG paper (arXiv:2005.11401), OpenAlex
returned 18 forward citations. The combined OpenAlex + SS path returned 43 — a 2.4×
increase. The SS-only additions were real, bibliographically distinct papers that
had cited the arXiv version.

---

## Running a citation-graph expansion

### CLI

```bash
# Forward + backward, 8 papers per seed per direction, LLM screen at 0.5 threshold
perspicacite -c config.yml expand-kb \
  --kb my-kb \
  --direction both \
  --max-per-seed 8 \
  --min-year 2020 \
  --screen llm \
  --screen-threshold 0.5

# Dry-run: see what would be ingested without actually ingesting
perspicacite -c config.yml expand-kb --kb my-kb --direction forward --dry-run

# Backward only (intellectual lineage), no screening
perspicacite -c config.yml expand-kb --kb my-kb --direction backward
```

### MCP

```python
await expand_kb_via_citations(
    kb_name="my-kb",
    direction="both",         # "forward" | "backward" | "both"
    max_per_seed=8,
    min_year=2020,
    screen_method="llm",      # "bm25" | "llm" | None
    screen_threshold=0.5,
)
```

The Semantic Scholar fallback fires automatically when arXiv seeds are detected —
there is no separate flag to enable it. To disable it (e.g., to stay within SS API
rate limits), set `include_semantic_scholar: false` in the expand-kb call or config.

---

## Filter and score parameters

These parameters apply identically to `expand-kb` and `search-to-kb`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--min-year` | none | Drop papers published before this year |
| `--max-year` | none | Drop papers published after this year |
| `--min-citations` | 1 | Drop papers with fewer citations than this |
| `--require-abstract` | false | Drop papers without an abstract |
| `--screen llm\|bm25` | none | Run a relevance screen after filtering |
| `--screen-threshold` | 0.3 | Minimum screen score to include a paper |
| `--max-per-seed` | 50 | Hard cap on papers fetched per seed paper |

The `cite_graph` section of `config.yml` provides default values for these parameters
when not specified on the command line:

```yaml
cite_graph:
  min_year_offset: 7       # Drop papers older than now - 7 years
  min_citations: 1
  max_papers: 50
  venue_denylist: []       # e.g. ["predatory-journal-name"]
  w_citations: 0.30        # Scoring weights for ranking candidates
  w_recency:   0.20
  w_oa:        0.20
  w_match:     0.30
```

---

## Deduplication

Papers already in the KB are detected by DOI before the filter stage and skipped. This
makes expansion runs idempotent: running the same expand-kb command twice adds nothing
the second time (assuming no new papers appeared in OpenAlex or SS between runs).

---

## Rate limits

OpenAlex has a generous anonymous rate limit (10 requests/second). SS provides a
higher limit with an API key — configure it under:

```yaml
pdf_download:
  semantic_scholar_api_key: "..."
```

Without a key, SS requests are rate-limited at ~1 req/second; large KBs may trigger
429 responses. The SS fallback client retries with exponential backoff up to
`pdf_download.max_retries` (default 3) before skipping that seed's SS walk.

---

## Related topics

- [guides/expand-via-citations.md](../guides/expand-via-citations.md) — step-by-step
  expansion workflow
- [concepts/knowledge-bases.md](knowledge-bases.md) — how expanded papers are stored
- [reference/cli.md](../reference/cli.md) — `expand-kb` and `enrich-cite-graph` flags
- [reference/paper-source-enum.md](../reference/paper-source-enum.md) — how
  `CITATION_FOLLOW`, `OPENALEX`, and `SEMANTIC_SCHOLAR` source values are assigned
