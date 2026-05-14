# ORCID author disambiguation — design spec

**Wave 4.4 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Map a free-text author name (`"Smith J."`,
`"John Smith"`, `"J. Smith"`) to a canonical ORCID ID via OpenAlex.
Returns the candidate with the most works (best disambiguation
signal we get without affiliation context) plus a confidence score.

## Scope of v1

In scope:
- Standalone resolver module — pure function + SQLite cache.
- OpenAlex API only (no auth needed, generous limits).
- Returns `AuthorResolution` or `None` (ambiguous / not found).
- Confidence based on top-2 candidate spread.

Out of scope (followups):
- Wiring into paper-ingest paths (each paper's authors get resolved
  automatically). Mechanical change once the helper is proven.
- ORCID API (requires auth, more strict — not necessary for v1).
- Affiliation-based disambiguation.
- Bulk-resolve endpoint.

## Architecture

```python
from perspicacite.pipeline.orcid import AuthorResolver, AuthorResolution

resolver = AuthorResolver(cache_path=Path("data/orcid_cache.db"))
res = await resolver.resolve("Smith J.")
# res = AuthorResolution(
#     orcid="0000-0001-...",
#     display_name="John Smith",
#     works_count=147,
#     confidence=0.74,
# )
# or None if ambiguous / not found.
```

## OpenAlex query

```
GET https://api.openalex.org/authors?search={url-encoded-name}&per_page=5
```

Response shape (abbreviated):

```json
{
  "results": [
    {
      "id": "https://openalex.org/A1234567890",
      "orcid": "https://orcid.org/0000-0001-...",
      "display_name": "John Smith",
      "works_count": 147,
      "cited_by_count": 5421
    },
    ...
  ]
}
```

We strip the `https://orcid.org/` prefix to keep just the ID
(`0000-0001-...`).

## Confidence scoring

Compare the top two results' `works_count`:

```
top1, top2 = top_two_works_counts (top2=0 if only one result)
spread = (top1 - top2) / top1     # 0..1
confidence = spread
```

Threshold: returns `None` when `confidence < 0.20` OR top result has
no ORCID (some authors are in OpenAlex without one — useless for our
purpose).

Rationale: a 5-paper author with one 4-paper rival is genuinely
ambiguous (spread=0.20); a 100-paper author with a 3-paper rival is
clearly the right match (spread=0.97). The threshold tunes the
recall/precision balance. Document the knob.

## SQLite cache

`data/orcid_cache.db`:

```sql
CREATE TABLE orcid_cache (
    name          TEXT PRIMARY KEY,
    orcid         TEXT,           -- nullable when resolution returned None
    display_name  TEXT,
    works_count   INTEGER,
    confidence    REAL,
    created_at    INTEGER
);
```

- Hit → return `AuthorResolution` directly (no HTTP).
- `orcid=NULL` rows cache "no good match" — avoid hammering the API
  on names that don't disambiguate.
- TTL = 30 days by default (authors do add ORCIDs / accrue works).
  `ttl_days=0` disables expiry.

## Components

| File | Change |
|---|---|
| `src/perspicacite/pipeline/orcid.py` (new) | `AuthorResolver`, `AuthorResolution`, OpenAlex client, SQLite cache. |
| `src/perspicacite/config/schema.py` | Add `kb.orcid_cache_path: Path = Path("data/orcid_cache.db")`, `kb.orcid_cache_ttl_days: int = 30`, `kb.orcid_confidence_threshold: float = 0.20`. |
| `tests/unit/test_orcid_resolver.py` (new) | Mocked HTTP responses → resolution shape, ambiguity handling, cache hit, TTL expiry. |
| `docs/orcid-disambiguation-2026-05-14.md` (new) | Operator guide. |

## Behaviour contract

- Network failure → log warning, return `None` (don't propagate; we
  never let ORCID lookup break an ingest).
- Empty `results` array → cache `None` entry, return `None`.
- Top result has no `orcid` → cache `None`, return `None`.
- Confidence below threshold → cache `None`, return `None`.
- `name=""` or whitespace → `None` immediately, no HTTP.

## Test plan

- `test_resolves_unambiguous_author`
- `test_returns_none_when_top_lacks_orcid`
- `test_returns_none_when_confidence_below_threshold`
- `test_returns_none_when_results_empty`
- `test_caches_resolution_to_db`
- `test_cache_hit_avoids_http`
- `test_cache_negative_result_avoids_http`
- `test_ttl_expiry_re_queries`
- `test_blank_name_returns_none_without_http`
- `test_network_failure_returns_none_doesnt_raise`

## Followups

- Wire into ingest: after a Paper is constructed, resolve each
  author's ORCID and stamp `Author.orcid`. Mechanical, ~20 lines.
- Bulk-resolve endpoint that batches up to 25 names per OpenAlex
  call (the search endpoint supports it via OR filters).
- ORCID API as a second lookup with auth (sometimes returns more
  accurate matches for sparse OpenAlex authors).
- Affiliation-context disambiguation: pass the paper's institutional
  affiliations into the resolver so `J. Smith @ MIT` beats `J. Smith
  @ Oxford` correctly.
