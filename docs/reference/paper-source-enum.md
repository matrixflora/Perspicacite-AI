# PaperSource Enum

`PaperSource` is a string enum in `src/perspicacite/models/papers.py` that records
the origin database or ingestion path for every `Paper` object in the system. It
appears in:

- `Paper.source` — the source value stamped at ingest time
- KB statistics (`GET /api/kb/{name}/stats`) — breakdown of papers by source
- Provenance traces — source database of each retrieved paper
- SQLite paper metadata table

---

## Values

| Value | String | When assigned |
|-------|--------|--------------|
| `OPENALEX` | `"openalex"` | Paper discovered and fetched primarily via OpenAlex (e.g., DOI-based lookup, citation-graph expansion) |
| `PUBMED` | `"pubmed"` | Paper from PubMed/NCBI search (`pubmed-search` command, `entrez` fetcher) |
| `ARXIV` | `"arxiv"` | Paper from the arXiv API (arXiv-specific fetch path, arXiv HTML retrieval) |
| `CROSSREF` | `"crossref"` | Paper metadata resolved via Crossref (DOI metadata enrichment, CrossRef-primary path) |
| `SEMANTIC_SCHOLAR` | `"semantic_scholar"` | Paper fetched directly via the Semantic Scholar API (SS fallback in cite-graph expansion, direct SS search) |
| `BIBTEX` | `"bibtex"` | Paper ingested from a user-provided `.bib` file |
| `SCILEX` | `"scilex"` | Paper returned by a SciLEx multi-database fan-out search |
| `WEB_SEARCH` | `"web_search"` | Legacy value; kept for backward compatibility. No ingestion path currently assigns this. |
| `USER_UPLOAD` | `"user_upload"` | Paper from a direct user upload (UI or API upload of a PDF without a DOI) |
| `CITATION_FOLLOW` | `"citation_follow"` | Paper added by following a citation link (pre-migration legacy value; now superseded by `OPENALEX`/`SEMANTIC_SCHOLAR` for new ingest) |
| `LOCAL` | `"local"` | Paper ingested from the local file system (`ingest-local` command or MCP `ingest_local_documents`) |

---

## Migration note (2026-05-15)

Before the PaperSource migration, most papers ingested via the download pipeline were
stamped `WEB_SEARCH` regardless of which API actually returned them. The migration
(commit `feat(models): thread PaperSource through CrossRef + cite-graph adapters` and
related) changed every `Paper` construction site to stamp the true origin:

- CrossRef enrichment → `CROSSREF`
- OpenAlex citation edges → `OPENALEX`
- Semantic Scholar citation edges → `SEMANTIC_SCHOLAR`
- arXiv-primary fetch → `ARXIV`
- PubMed search → `PUBMED`

The `WEB_SEARCH` value is preserved for backward compatibility (existing SQLite rows
from before the migration keep their value) but is no longer assigned by any ingestion
path in v2.0.0+.

If you have a KB created before 2026-05-15, papers in it may carry `web_search` as
their source. Re-ingesting them (by deleting and rebuilding the KB from the same
`.bib`) will assign the correct source values.

---

## Usage in code

```python
from perspicacite.models.papers import Paper, PaperSource

paper = Paper(
    id="10.1038/s41586-023-06924-6",
    title="...",
    source=PaperSource.OPENALEX,
    # ...
)

# Check source in provenance filtering
if paper.source in (PaperSource.ARXIV, PaperSource.SEMANTIC_SCHOLAR):
    # arXiv-seeded paper — may benefit from SS fallback cite-graph
    ...
```

---

## Related topics

- [concepts/citation-graph.md](../concepts/citation-graph.md) — how `OPENALEX` and
  `SEMANTIC_SCHOLAR` values are assigned during cite-graph expansion
- [concepts/provenance.md](../concepts/provenance.md) — where `source` appears in
  retrieval traces
- [VISION.md](../VISION.md) — the design principle behind honest sourcing
