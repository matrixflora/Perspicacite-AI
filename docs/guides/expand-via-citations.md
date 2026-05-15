# Expand a KB via Citations

Once you have a seed knowledge base, you can grow it by following citation links —
forward (papers that cite yours) and backward (papers yours cite). This guide walks
through the `expand-kb` command and the corresponding MCP tool.

---

## Prerequisites

- An existing KB with at least a few papers that have DOIs
- No SciLEx dependency — citation expansion uses OpenAlex directly
- Optional: a Semantic Scholar API key for higher rate limits on the SS fallback
  (see [concepts/citation-graph.md](../concepts/citation-graph.md))

---

## Basic expansion

```bash
# Forward citations: papers that cite the papers in your KB
perspicacite -c config.yml expand-kb --kb diamond_sensors --direction forward

# Backward citations: papers that your KB papers cite
perspicacite -c config.yml expand-kb --kb diamond_sensors --direction backward

# Both directions at once
perspicacite -c config.yml expand-kb --kb diamond_sensors --direction both
```

---

## Filtering and screening

The same filter stack as `search-to-kb` applies:

```bash
perspicacite -c config.yml expand-kb \
  --kb diamond_sensors \
  --direction both \
  --max-per-seed 8 \
  --min-year 2020 \
  --min-citations 5 \
  --screen llm \
  --screen-threshold 0.5
```

`--max-per-seed N` caps how many papers are fetched per seed paper per direction.
With a KB of 30 papers and `--max-per-seed 8` and `--direction both`, the maximum
possible candidates before filtering is 30 × 8 × 2 = 480.

---

## Dry-run

Preview what would be ingested without actually running the pipeline:

```bash
perspicacite -c config.yml expand-kb \
  --kb diamond_sensors \
  --direction forward \
  --min-year 2021 \
  --dry-run
```

The output lists candidate DOIs with titles, citation counts, and whether they are
already in the KB.

---

## Semantic Scholar fallback for arXiv seeds

If any seed paper in your KB has an arXiv ID, Perspicacité automatically fires a
parallel Semantic Scholar walk alongside the OpenAlex walk. The results are merged
and deduplicated before the filter stage.

This is relevant for machine learning, physics, and computational biology KBs where
many papers circulated as preprints before journal publication. See
[concepts/citation-graph.md](../concepts/citation-graph.md) for a detailed explanation
of why this matters and the measured improvement.

No configuration is needed — the fallback is always-on when arXiv seeds are detected.

---

## Via MCP

```python
await expand_kb_via_citations(
    kb_name="diamond_sensors",
    direction="both",           # "forward" | "backward" | "both"
    max_per_seed=8,
    min_year=2020,
    min_citations=5,
    screen_method="llm",        # "bm25" | "llm" | None
    screen_threshold=0.5,
)
```

The tool returns a summary: `{"added_papers": N, "skipped_duplicate": M, ...}`.

---

## Enriching the cite-graph metadata (without ingest)

A separate `enrich-cite-graph` command enriches the citation metadata of papers
already in the KB without adding new papers:

```bash
perspicacite -c config.yml enrich-cite-graph --kb my-kb
```

This fetches updated citation counts and reference lists from OpenAlex for papers
already in the KB and updates the SQLite metadata. Useful when you want fresh citation
counts without a full expansion run.

---

## Iterative expansion

Citation expansion is designed to be run iteratively. After the first expansion, the
KB now contains the neighbors of your original seeds. Running expansion again follows
links from those neighbors, effectively doing a 2-hop walk:

```bash
# First expansion: 1 hop from original seeds
perspicacite -c config.yml expand-kb --kb diamond_sensors --direction forward

# Second expansion: 1 hop from the first expansion's papers
# (papers already in the KB are skipped, so this adds the 2-hop neighborhood)
perspicacite -c config.yml expand-kb --kb diamond_sensors --direction forward
```

Use `--min-year` and `--screen llm` to prevent the KB from growing unboundedly.

---

## Related topics

- [concepts/citation-graph.md](../concepts/citation-graph.md) — how the walk and SS
  fallback work in detail
- [guides/search-to-kb.md](search-to-kb.md) — alternative: grow from a fresh query
- [reference/cli.md](../reference/cli.md) — `expand-kb` and `enrich-cite-graph` flags
- [reference/mcp-tools.md](../reference/mcp-tools.md) — `expand_kb_via_citations` and
  `enrich_kb_from_cite_graph_tool` tool signatures
