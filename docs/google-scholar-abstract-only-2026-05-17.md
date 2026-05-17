# Google Scholar + Abstract-Only KB Mode (2026-05-17)

Two search-pipeline improvements shipped together.

---

## Google Scholar via Playwright

### Setup

```bash
uv pip install -e "[browser]"
playwright install chromium   # ~150 MB download
```

Then in `config.yml`:

```yaml
google_scholar:
  enabled: true          # must be true
  delay_seconds: 2.0     # keep >= 2.0 to avoid CAPTCHA
  headless: true

search:
  enabled_providers:
    - scilex
    - europepmc
    - google_scholar      # add this line
```

### What it does

Launches headless Chromium, navigates to `scholar.google.com/scholar?q=...`,
extracts result cards (title, authors, year, abstract snippet, DOI when visible)
and returns them as `Paper` objects in the standard aggregator pipeline.

Year filters are passed via `as_ylo` / `as_yhi` Scholar URL parameters.

### Limitations

- **Rate limiting**: Scholar detects automated access. The 2-second delay between
  pages works for personal/research use (< ~50 queries/day). For higher volume,
  increase `delay_seconds` or use a residential proxy.
- **CAPTCHA**: If Scholar shows a CAPTCHA, the provider returns `[]` and logs
  `google_scholar_captcha_detected`. Switch to a fresh IP or wait ~1 hour.
- **DOIs**: Scholar doesn't always link to `doi.org`. Papers without a visible
  DOI get a synthetic ID (`scholar:XXXXXX`) and cannot be directly ingested
  by DOI. Use `get_paper_content` with the Scholar URL if you need full text.
- **Tier**: Uses `flaky` tier in the aggregator — gets 45 s timeout (2.25 ×
  20 s default) and failures don't block other providers.

### KB dedup via `search_literature`

The `search_literature` MCP tool now accepts `exclude_kb`:

```python
await client.call_tool("search_literature", {
    "query": "CRISPR base editing",
    "max_results": 20,
    "exclude_kb": "my-crispr-kb",   # papers already in KB are removed
})
```

Useful workflow: search → see what's new → `add_dois_to_kb` for the new hits.

---

## Abstract-only KB mode

For large literature surveys (200–2000 DOIs), PDF download is the bottleneck.
Abstract-only mode skips it entirely.

### Configuration

In `config.yml`:

```yaml
knowledge_base:
  ingest_mode: "abstract_only"   # or "auto" (default) or "full_text"
```

### Speed comparison

| Mode | 100 DOIs | 500 DOIs |
|------|----------|----------|
| `auto` | ~8 min | ~40 min |
| `abstract_only` | ~90 s | ~7 min |

(Network-dependent; measured on a 50 Mbps connection.)

### When to use

- **Use `abstract_only`** for: initial corpus mapping, screening large
  candidate sets, topic modelling, literature survey breadth pass.
- **Use `auto`** for: final KB population, deep RAG Q&A, citation
  extraction, methodology detail queries.

### Retrieval impact

`abstract_only` papers embed a shorter text (title + abstract, ~200-400 tokens
vs 4000-20000 tokens for full text). Retrieval recall drops for detailed
methodology questions but is comparable for topic-level queries.

---

## Files

| File | Purpose |
|------|---------|
| `src/perspicacite/search/google_scholar_playwright.py` | Playwright-based Scholar provider |
| `src/perspicacite/config/schema.py` | `GoogleScholarConfig`, `KnowledgeBaseConfig.ingest_mode` |
| `src/perspicacite/search/domain_aggregator.py` | Scholar wired into `build_aggregator` |
| `src/perspicacite/pipeline/download/unified.py` | `retrieve_paper_content(abstract_only=True)` |
| `src/perspicacite/pipeline/search_to_kb.py` | Passes `abstract_only`, counts abstract results as success |
| `src/perspicacite/mcp/server.py` | `search_literature(exclude_kb=...)` dedup |
