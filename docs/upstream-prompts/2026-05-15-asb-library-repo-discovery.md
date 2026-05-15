# Upstream brief for AgenticScienceBuilder — library→repo discovery via GitHub search

**Date written:** 2026-05-15
**From:** Perspicacité-AI maintainers
**To:** AgenticScienceBuilder (ASB) maintainers / next ASB session
**Status:** Suggestion, no PR opened

This is a self-contained brief. Hand it to a fresh agent or new
contributor working in `~/git/AgenticScienceBuilder` and they can
act on it without reading any further Perspicacité context.

---

## 1. What we'd like ASB to consider building

A new step in ASB's skill-enrichment pipeline that **discovers sister
GitHub repositories for each library/tool listed on a skill**, using
the GitHub Search API. The output is stored on the skill (or on a
sidecar manifest) as a list of *related-repo locators* — full names,
star counts, topics, last-push timestamp, and the matching query.

The discovery phase **does not need to clone or download** the
candidate repos. GitHub Search returns enough metadata + a snippet to
rank candidates. ASB's existing capsule builder already handles the
download when a capsule is actually constructed. So this enrichment
step is cheap to run as part of long-term skill maintenance — and a
good fit for ASB because skills live there long-term, not in
downstream KB consumers.

The result is reusable by any downstream consumer of an ASB skill
bundle (Perspicacité-AI is one such consumer; others can be too).

## 2. Why this lands in ASB, not Perspicacité

Perspicacité is a literature-RAG system. It consumes skill bundles
and builds knowledge bases from them. After thinking through the
boundaries, both teams agreed:

- **GitHub repo discovery for a library is a *skill curation* task.**
  It belongs where skills are authored and maintained — that's ASB.
- **Citation propagation (which articles cite this library) is a
  *literature retrieval* task.** It belongs where papers and RAG
  live — that's Perspicacité.

Perspicacité is shipping the cite-graph side this cycle (spec at
`Perspicacite-AI/docs/superpowers/specs/2026-05-15-cite-graph-enrichment-design.md`)
and would consume any ASB-side repo enrichment via the existing
GitHub-KB / skill-bundle ingest path
(`Perspicacite-AI/docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`).

## 3. Prior-art check we did

We grepped ASB and found:

- `/repos/{name}` calls in
  `src/agentic_science_builder/enrichment.py:386-586`,
  `list_parser.py:286-300`,
  `skill_resource_fetcher.py:254`. README + tree + metadata only.
- `SciSkill.related_tools: list[ToolUsage]` field in
  `src/agentic_science_builder/llm_schemas.py:478` — populated from
  paper-text LLM extraction, **not** from GitHub search.
- `_parse_related_tools_urls` in
  `src/agentic_science_builder/skill_pack_v3.py:891` — parses markdown
  tables for related-tool URLs in supplied docs.

No `/search/repositories` or `/search/code` calls anywhere in ASB.
So adding this is genuinely additive — not duplicating existing logic.

## 4. Proposed shape

### 4.1 New module

```
src/agentic_science_builder/github_search.py     # NEW
```

Exposes a thin client over the GitHub Search REST API. Two methods:

```python
class GitHubSearchClient:
    def __init__(self, *, token: str | None, http: requests.Session): ...

    def search_repositories(
        self, *, query: str, language: str | None,
        min_stars: int, per_page: int, max_pages: int,
    ) -> list[RepoHit]: ...

    def search_code(
        self, *, query: str, language: str | None,
        per_page: int, max_pages: int,
    ) -> list[RepoHit]:
        # Requires auth. Skipped (with structured warning) when no token.
        ...
```

`RepoHit` carries: `full_name, description, stars, pushed_at, license_spdx,
language, topics, html_url, default_branch, matched_via, matched_query,
score, score_breakdown`.

Rate-limit awareness: honour `X-RateLimit-Remaining` and `Retry-After`
headers; never burst past the documented 30 req/min cap on search
endpoints when authenticated, or 10 req/min unauth.

### 4.2 New enrichment step in the existing pipeline

Wire the client into a new function alongside
`enrichment.enrich_github(...)`:

```python
def enrich_related_repos(
    skill: SciSkill,
    *,
    tools: list[str],
    mode: Literal["library", "symbol", "both"] = "library",
    config: EnrichmentConfig,
    client: GitHubSearchClient,
) -> list[RepoHit]:
    """For each tool, fan out to /search/repositories and (when token
    present) /search/code. Dedup by full_name. Filter by stars,
    recency, license, language. Score. Return top-N."""
```

The result is written to the skill's resource index (alongside
`SciSkill.related_tools`) as a new field, e.g.
`SciSkill.related_repos: list[RelatedRepo]`. Existing consumers
ignoring the field continue to work.

`RelatedRepo` should record at minimum:
- `full_name`, `html_url`, `default_branch`
- `stars`, `pushed_at`, `license_spdx`
- `matched_via`, `matched_query`
- `score`, `score_breakdown` (so downstream tooling can re-filter)

No download. The candidate snippet from `/search/code` is enough to
verify the match was meaningful; full code lives in GitHub until the
capsule builder pulls it.

### 4.3 Symbol mode (optional)

When the skill carries a symbol index (e.g. from
[Perspicacité's sub-project A](../Perspicacite-AI/docs/superpowers/specs/2026-05-15-code-and-multimodal-retrieval-design.md)
shape — `symbols.jsonl` with `(name, kind, file_path, signature)`),
pick the most *distinctive* symbols (TF-IDF over symbol names — bias
against generic names like `run`, `main`, `init`) and run code-search
for each. Cap K = 10 symbols, 30 hits per symbol. Bound the total
search budget per skill.

ASB doesn't have a symbol index today, so symbol mode is a follow-up;
library mode is the v1.

### 4.4 Scoring

Same shape Perspicacité had drafted before deciding this should live
in ASB:

```
score =
    w_stars   * normalize_stars(stars)                # log-scaled
  + w_recency * recency_score(pushed_at)              # 0..1, 18-mo half-life
  + w_match   * keyword_match(readme, tools)          # 0..1, BM25-ish
  + w_topic   * (1.0 if any(tool in topics) else 0.0)
  + w_license * (1.0 if has_oss_license else 0.0)
```

Default weights `(0.30, 0.20, 0.25, 0.15, 0.10)` summing to 1.0.

Default filter caps: `min_stars=20`, `max_age_months=36`,
`require_license=True`, license allowlist `[MIT, BSD-2/3-Clause,
Apache-2.0, MPL-2.0, GPL-3.0, LGPL-3.0]`, `max_repos_per_tool=10`,
`max_total_repos=50`.

### 4.5 Dry run

Provide a `--dry-run` flag on whatever CLI surface wraps this so the
skill author can inspect ranked candidates with score breakdowns
before committing them to the skill's manifest. ASB's `cli.py` is the
natural home.

## 5. Integration with Perspicacité

Perspicacité consumes the result automatically: its GitHub-KB ingest
path reads `bundle.yml`'s repo list. If ASB writes the discovered
related repos into the bundle's effective repo list (or into a
companion `bundle_related_repos.yml`), Perspicacité will pick them up
on next ingest with no further changes on its side.

Suggested chunk-provenance tagging on the Perspicacité side:
`source_via="bundle"` for declared repos vs
`source_via="bundle_related"` for ASB-discovered ones. We can add the
literal to our `ChunkMetadata` enum in lockstep with ASB's release —
just send us the field name you settle on.

## 6. Risks / mitigations to think about

- **Quality drift.** Mitigated by the filter + scoring + dry-run
  preview, plus the option to weight an LLM rerank pass on top.
- **API exhaustion.** Mitigated by per-run budgets and `Retry-After`
  honouring.
- **License contamination.** Mitigated by SPDX allowlist + `require_license=True`.
- **Surprise volumes for ubiquitous libraries** (numpy, pandas).
  Mitigated by per-tool caps; recommend documentation guidance:
  "don't enrich on platform-level libraries."

## 7. Estimate

We sized this at ~2.5–3 days when we thought we'd build it ourselves.
Probably faster in ASB because the existing fetch/cache/manifest
infrastructure is already there to plug into.

## 8. Asks of ASB maintainers

1. Read this and tell us if the boundary makes sense to you.
2. If yes: open an ASB issue / plan with the file changes you'd want
   to make. Happy to review.
3. Settle on a field name for the discovered list so Perspicacité can
   align its `source_via` literal.

Thanks — the cleaner separation makes both projects easier to
maintain.

— Perspicacité-AI maintainers, 2026-05-15
