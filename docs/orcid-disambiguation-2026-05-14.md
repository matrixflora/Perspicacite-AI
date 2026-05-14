# ORCID disambiguation — operator guide (2026-05-14)

Wave 4.4 of the framework-hardening roadmap. Map free-text author
names to canonical ORCID IDs via OpenAlex.

## API

```python
from pathlib import Path
from perspicacite.pipeline.orcid import AuthorResolver

resolver = AuthorResolver(
    cache_path=Path("data/orcid_cache.db"),
    ttl_days=30,
    confidence_threshold=0.20,
)
res = await resolver.resolve("Smith J.")
if res is not None:
    print(res.orcid, res.display_name, res.confidence)
```

`AuthorResolution` fields:

| Field | Meaning |
|---|---|
| `orcid` | `"0000-0001-..."` (URL prefix stripped) |
| `display_name` | OpenAlex's canonical display name |
| `works_count` | Number of works in OpenAlex |
| `confidence` | `(top1 - top2) / top1` — works-count spread |

`None` returns when:

- Name is blank.
- OpenAlex returns no results.
- Top result has no ORCID.
- Confidence < `confidence_threshold` (ambiguous).
- Network / HTTP failure (logged, not raised).

## Confidence threshold tuning

The default `0.20` accepts most reasonable matches and rejects
genuinely ambiguous ones (e.g., two "J. Smith" with similar
publication counts). Raise to `0.50` for high-precision use cases
(citations, ground-truth labels); lower to `0.10` for high-recall
exploratory work.

## Caching

A SQLite cache at `data/orcid_cache.db` stores every resolution —
positive and negative — for 30 days by default. Negative entries
prevent the resolver from hammering OpenAlex on names that don't
disambiguate.

```yaml
kb:
  orcid_cache_path: data/orcid_cache.db
  orcid_cache_ttl_days: 30           # 0 = forever
  orcid_confidence_threshold: 0.20
```

Manual cache clear:

```bash
rm data/orcid_cache.db
```

Selective by name:

```bash
sqlite3 data/orcid_cache.db \
  "DELETE FROM orcid_cache WHERE name LIKE 'J. Smith%';"
```

## Scope today

- **Module is wired**: `pipeline/orcid.py` resolves on demand.
- **Not wired into ingest**: today's `ingest_dois_into_kb` doesn't
  call the resolver. Wiring is mechanical (~20 lines per ingest
  path) and lives in a separate follow-up so this PR stays focused.

## API rate limits

OpenAlex's public API requires no auth and allows generous traffic
(no documented per-IP limit at small scale). The resolver respects
the suggested polite-pool conventions by:

- Setting `User-Agent` via httpx defaults (no custom override yet).
- Caching aggressively so we never re-query the same name within 30
  days.

For production-grade traffic, add a `mailto=...` query param. That's
a documented followup.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/orcid.py` | `AuthorResolver`, `AuthorResolution` |
| `src/perspicacite/config/schema.py` | `orcid_*` fields on `KnowledgeBaseConfig` |

## Followups

- Wire into ingest (Paper authors get `orcid` stamped automatically).
- Bulk endpoint (batch 25 names per OpenAlex call).
- ORCID API as a secondary lookup with auth.
- Affiliation-context disambiguation.
- Add `mailto=` for the OpenAlex polite-pool.
