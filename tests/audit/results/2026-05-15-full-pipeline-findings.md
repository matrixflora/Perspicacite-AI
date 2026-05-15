# 2026-05-15 — Full-pipeline live audit findings

**Date:** 2026-05-15
**Harness:** `tests/audit/run_full_pipeline_audit.py`
**Output:** `tests/audit/results/full-pipeline-audit-20260515-154148.{json,md}`
**Git SHA at run:** `a3acae0` (post P1/P2/P3 batch)

## Scope

Five phases exercising the user-named surfaces. Mapping (none of the literal
names existed in the codebase except ASB):

| User name | Implementation |
|---|---|
| Lab | OpenAlex metadata fetch + `chunking_dispatch.chunk_document` |
| ASB | `capsule_builder.write_{metadata,blocks,resources}` + `symbol_index.write_chunks_symbols` |
| KGmemory | `pipeline/cite_graph.py` + `memory/session_store.py` + `provenance/{collector,store,context}.py` |
| Manuscript | `models/rag.py` (RAGRequest/Response/StreamEvent + Figure/Code attachments) + `rag/modes/literature_survey.py` init |
| Audit | `llm/budget.py` + `rag/kb_router.py` (bm25s cache) + chunking-malformed + figure-thumbnail end-to-end |

Three real papers exercised the harness:
1. AlphaFold (Nature DOI, 44 072 citations)
2. RAG / Lewis et al. (arXiv DOI 10.48550/arXiv.2005.11401)
3. Attention Is All You Need (arXiv DOI 10.48550/arXiv.1706.03762)

---

## 🐛 Critical / blocking findings

### 1. ProvenanceStore is unusable without SessionStore

**Discovered:** When `ProvenanceStore(db_path=..., sidecar_dir=...)` is
instantiated against a fresh SQLite file, **`save()` silently drops every
record** and **`get_for_message()` raises `OperationalError: no such
table: provenance`**.

```
File "src/perspicacite/provenance/store.py", line 90, in get_for_message
    "SELECT * FROM provenance WHERE message_id = ?", (message_id,)
sqlite3.OperationalError: no such table: provenance
```

**Root cause:** The `provenance` table is created by
`SessionStore.init_db()` (`src/perspicacite/memory/session_store.py:56`).
`ProvenanceStore.__init__` never inspects or initialises the schema. The
intent is clearly that `SessionStore` boots first and shares the DB file,
but this contract is **undocumented and silently fails** when violated.

**Severity:** **High.** `ProvenanceStore.save()` swallows the
`OperationalError` with a `try/except` + `logger.warning("provenance_save_failed")`
and returns normally — so any path that uses ProvenanceStore without first
calling `SessionStore.init_db()` will log a tiny warning while losing data.

**Fix recommendation:**
1. Add `async def init_db()` to `ProvenanceStore` that runs the same
   `CREATE TABLE IF NOT EXISTS provenance (...)` (move the SQL into a
   shared module so both stores see it).
2. Call it from `ProvenanceStore.__init__` lazily, or require an explicit
   `init_db()` call (matching `SessionStore`'s pattern).
3. In `save()`, escalate `OperationalError` (schema missing) instead of
   swallowing it.

---

### 2. `SourceReference.authors: str` cannot hold a list of authors

**Discovered:** Constructing a `SourceReference` with `authors=["Jumper et
al."]` fails Pydantic v2 validation:

```
ValidationError: 1 validation error for SourceReference
authors
  Input should be a valid string [type=string_type, input_value=['Jumper et al.'], input_type=list]
```

The field is `authors: str` (single string). All call sites must
pre-join multi-author lists into one comma-separated string, which:
- Loses structured access (counting authors, filtering by surname)
- Conflicts with `Paper.authors: list[Author]` upstream
- Causes silent ergonomics drift between API surfaces

**Severity:** Medium-high. Schema mismatch with the rest of the codebase.

**Fix recommendation:**
Change to `authors: list[str] = Field(default_factory=list)` (or richer:
`list[Author]`). Migrate call sites; add a Pydantic `field_validator` that
accepts comma-joined strings for backward compatibility.

---

### 3. arXiv-DOI cite-graph still returns 0 hits via the DOI path

**Discovered:** All three arXiv-DOI papers in the harness returned 0
cite-graph hits via the DOI path:

```
snowball_oa_seed_miss doi=10.48550/arXiv.2005.11401 status=404 → 0 hits
snowball_oa_seed_miss doi=10.48550/arXiv.1706.03762 status=404 → 0 hits
```

The `--openalex-id` flag (Task 4 from the P1/P2/P3 batch) returns
10 hits in ~1–2 s for each, but the DOI happy-path is broken.

**Root cause:** Task 3's arXiv-id fallback only covers
`openalex_id_for_doi`. The cite-graph orchestrator's seed fetch goes
through `_fetch_seed_work` in `pipeline/snowball.py`, which doesn't have
the same fallback.

**Severity:** Medium. Already flagged in the post-batch audit append
(commit `a3acae0`).

**Fix recommendation:** Apply the same `parse_arxiv_doi` →
`ids.arxiv:<id>` filter fallback inside `_fetch_seed_work`.

---

### 4. `BudgetTracker.__init__()` rejects expected kwargs

**Discovered:** A natural-looking call

```python
BudgetTracker(max_tokens=1000, max_cost_usd=1.0)
```

fails with

```
TypeError: BudgetTracker.__init__() got an unexpected keyword argument 'max_tokens'
```

The harness used the most obvious budget-tracking API but the real
signature is different. Either the actual signature is wrong, or it's
under-documented.

**Severity:** Low-medium. Not a runtime bug; reflects API drift / lack
of docs.

**Fix recommendation:** Inspect `src/perspicacite/llm/budget.py`,
either add the `max_tokens` / `max_cost_usd` kwargs (with sane
default-`None` "unlimited"), or update internal docs.

---

## 🟡 Schema gaps

### 5. `PaperSource` enum lacks PUBMED / ARXIV / OPENALEX / CROSSREF

**Current values** (`src/perspicacite/models/papers.py:10`):
```
BIBTEX, SCILEX, WEB_SEARCH, USER_UPLOAD, CITATION_FOLLOW, LOCAL
```

Yet the project clearly ingests arXiv, PubMed, OpenAlex, and Crossref
records. None of the major literature databases is a first-class
enum value. The fallback is `WEB_SEARCH` (semantically wrong) or
`CITATION_FOLLOW` (only for snowball hits).

**Severity:** Low. Causes confusion and forces type-erasure at ingest.

**Fix recommendation:** Add `OPENALEX`, `PUBMED`, `ARXIV`, `CROSSREF`
to the enum; thread them through the ingest entry points.

---

### 6. OpenAlex coverage of arXiv-only preprints is sparse

**Observation:** The OpenAlex record for the RAG paper (W3098425262)
correctly carries the title "Retrieval-Augmented Generation for
Knowledge-Intensive NLP Tasks", but reports only **18 citations** and
**no DOI link** — vs the Semantic-Scholar / Google-Scholar count
of ~7 000. OpenAlex's coverage of arXiv-only preprints is poor.

**Implication:**
- Cite-graph scoring leans heavily on `cited_by_count`; this signal is
  systematically biased *against* arXiv-only ML papers.
- The "topic-aware ranking" from the P0 batch is still the right
  mitigation; this just confirms the bias.

**Severity:** Low (external data quality).

**Fix recommendation:**
- Consider Semantic Scholar API as a cite-graph alternative for arXiv
  papers (separately spec'd, larger work).
- Or: weight `cited_by_count` lower when the paper has `doi: None`
  (i.e. when OpenAlex has poor metadata coverage for it).

---

### 7. `KBRouteHit` is a custom return type with no clean unwrap interface

**Observation:** `route_kbs(...)` returns `list[KBRouteHit]` where
`KBRouteHit(kb_name=, score=, reason=, sampled_titles=)` — but the
harness naturally tried to unwrap via tuple/dict patterns and silently
got wrong answers.

```
top_2 = [KBRouteHit(kb_name='biochem', score=1.0, ...),
         KBRouteHit(kb_name='ml_general', score=0.0, ...)]
```

The bm25s cache + scoring work correctly (biochem got score=1.0 for
"alphafold protein structure" — perfect signal), but the return-type
ergonomics caught the harness off guard.

**Severity:** Cosmetic / DX.

**Fix recommendation:** Add `KBRouteHit.__iter__` returning `(name, score)`
so destructuring `for name, score in route_kbs(...)` works, or expose a
`route_kbs_names(...)` helper for the very common "just names" case.

---

## ✅ Confirmed-working / no-regression

| Surface | Result |
|---|---|
| OpenAlex metadata fetch | 2/3 papers ok in <1 s (third was a wrong W-id in the harness — see below) |
| `chunking_dispatch.chunk_document` on real abstracts | OK — 374 / 1630 chars → 1 chunk (expected, within chunk_size) |
| `capsule_builder.write_{metadata, blocks, resources}` | OK — clean output for all three papers |
| `symbol_index.write_chunks_symbols` | OK — 8 / 36 symbols round-tripped per paper |
| Decorator-aware AST chunking (P1/P2 batch Task 1+2) | OK — kinds=`['class','classmethod','method','property','staticmethod']` on RAG file |
| `SessionStore` (conversations + messages + KB metadata) | OK — full round-trip with 2 messages |
| `cite-graph` via `--openalex-id` (Task 4) | OK — 10 hits for all 3 papers in 1.06–2.55 s |
| `StreamEvent.code_excerpt` / `figure_ref` factories | OK — including `thumbnail_b64` payload |
| `RAGRequest` with multi-kb-names | OK — `kb_names=['audit_kb','other_kb']` accepted |
| `LiteratureSurveyRAGMode.__init__` | OK (with `scilex_not_available` warning — soft dependency) |
| `openalex_id_for_doi` graceful miss | OK — returns None on fake DOI |
| Malformed-Python chunking fallback | OK — emits 1 fallback chunk, no exception |
| `collect_figure_refs` with capsule_root thumbnail load (P2 #7) | OK — `thumbnail_b64` populated from `figures/<fid>.png` |
| bm25s router cache | OK — corpus-fingerprint cache works; biochem→1.0, math→0.0 |

---

## 🔧 Harness-discovered issues vs real bugs

| # | Issue | Real bug? | Severity |
|---|---|---|---|
| 1 | ProvenanceStore standalone unusable | **Yes** | High |
| 2 | SourceReference.authors must be str not list | **Yes** | High |
| 3 | arXiv-DOI → cite-graph 0 hits via `_fetch_seed_work` | **Yes** | Medium |
| 4 | BudgetTracker API doesn't match natural kwargs | Probably yes | Low-Med |
| 5 | PaperSource missing arxiv/pubmed/openalex/crossref | **Yes** (schema gap) | Low |
| 6 | OpenAlex underreports arXiv-only citation counts | External data | Low |
| 7 | KBRouteHit ergonomics | Cosmetic | Low |
| – | Harness used `PaperSource.PUBMED` (doesn't exist) | Harness bug (related to #5) | – |
| – | Harness used W2963403868 (404) | Harness bug; demonstrates need for ID validation | – |

---

## Priority queue

Sorted by impact-per-effort:

1. **🔥 P0 — Add `ProvenanceStore.init_db()` + escalate schema errors.**
   Half-day. Eliminates silent data loss; decouples ProvenanceStore from
   SessionStore.

2. **🔥 P0 — `SourceReference.authors: list[str]` (or `list[Author]`).**
   Half-day. Fix the model, add a validator that coerces comma-joined
   strings for back-compat, migrate the 3-5 most-used call sites.

3. **P1 — arXiv-id fallback in `_fetch_seed_work`.** ~2h. Reuses Task 3's
   `parse_arxiv_doi`; mirrors the existing `openalex_id_for_doi`
   fallback. Closes the cite-graph DOI happy-path for ML papers.

4. **P1 — `PaperSource` enum: add OPENALEX, PUBMED, ARXIV, CROSSREF.**
   ~2h plus migration of ingest call sites.

5. **P2 — `BudgetTracker` API audit.** ~1h. Read current signature,
   either widen to accept `max_tokens`/`max_cost_usd` or document the
   actual API in the module docstring.

6. **P2 — `KBRouteHit.__iter__`.** ~30 min. Add the dunder so
   `for name, score in route_kbs(...)` works.

7. **P3 — Semantic Scholar API as cite-graph alternative for arXiv
   papers.** Larger; needs separate spec.

---

## Bottom line

- **5 confirmed real bugs** surfaced in 60 s of live audit, none caught
  by the 1 251-strong unit-test suite.
- **All 14 tests from the P1/P2/P3 batch continue to pass live**, and
  every functional surface from that batch is verified end-to-end on
  real papers.
- The most critical finding (**ProvenanceStore silent data loss**)
  exists because the only path that uses it (the live RAG pipeline) also
  uses SessionStore first — masking the bug for everyone except a
  standalone-instantiation audit. Worth fixing before any new code
  depends on the store.
- The `SourceReference.authors: str` schema mismatch is a five-minute
  Pydantic change that pays for itself the first time someone tries to
  count authors on a result.

---

## Update — 2026-05-15 evening: bug-fix batch landed

All six items from the priority queue (findings #1–#5, #7) shipped on
`main` via the subagent-driven plan
`docs/superpowers/plans/2026-05-15-audit-bug-fixes-batch.md`.

| Commit | Finding | Outcome |
|---|---|---|
| `c0743ab` | #1 ProvenanceStore.init_db | Standalone init works; `save()` now escalates `OperationalError`. Confirmed in re-run: audit harness now logs `provenance_save_schema_error` at error level instead of silently dropping. |
| `ddb5335` | #2 SourceReference.authors → list[str] | Field is `list[str]`; validator coerces None / list / str / "and"-separated. 9 new tests + 24 existing pass. |
| `ddf4d4a` | #3 `_fetch_seed_work` arXiv-id fallback | Code wired (audit log shows `snowball_oa_arxiv_fallback_miss` — fallback is now attempted). **Follow-up:** OpenAlex returns 400 on `filter=ids.arxiv:<id>` in production, despite the unit test mocks; the same syntax bug also exists in the prior batch's `openalex_id_for_doi`. Needs a small investigation of the right OpenAlex filter syntax for arXiv. |
| `775ba14` | #5 PaperSource enum | OPENALEX / PUBMED / ARXIV / CROSSREF added; PubMed ingest migrated. |
| `6fee4bc` | #4 BudgetTracker kwargs | `max_tokens` (combined cap) + `max_cost_usd` (alias) added without breaking existing fields. |
| `48def77` | #7 KBRouteHit.__iter__ | Destructuring `for name, score in route_kbs(...)` works. |

**Test totals after batch:** 27 new unit tests, 27/27 passing. 6 + 1 = 7 pre-existing failures (`test_local_docs_capsule_reader_route`, `test_mcp_multi_kb_passthrough`, `test_provenance_engine_wiring`, `test_zotero_ingest_worker`) are unchanged — confirmed identical before/after via stash diff.

**Remaining queue:**
- 🟡 **OpenAlex `ids.arxiv` filter syntax** (surfaced by the audit re-run, not in the original queue). Investigate the correct filter shape for arXiv preprints — `openalex_id_for_doi` also misses in production for the same reason.
- ⏭ **P3 — Semantic Scholar API as cite-graph alternative for arXiv papers** (separate spec, larger work — out of scope).
- ⏭ **Threading explicit PaperSource through every ingest adapter** (deliberate scope reduction in this batch).
