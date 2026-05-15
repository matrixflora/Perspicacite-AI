# 2026-05-15 — Second-round live audit findings

**Date:** 2026-05-15 (evening)
**Harness:** `tests/audit/run_second_round_audit.py`
**Output:** `tests/audit/results/second-round-audit-20260515-153640.{json,md}`
**Git SHA at run:** `9ad0baa` (post arXiv title.search fix)

## Scope

Three fresh papers from three different domains, deliberately disjoint
from the round-1 set (AlphaFold / RAG / Attention):

| Paper | Domain | DOI | Round-1 surface re-checked |
|---|---|---|---|
| CRISPR-Cas9 (Jinek 2012) | biomedical / chemistry | `10.1126/science.1225829` | Direct DOI resolution + cite-graph |
| LIGO GW150914 (Abbott 2016) | physics | `10.1103/PhysRevLett.116.061102` | Direct DOI resolution + cite-graph |
| GPT-3 (Brown 2020) | ML / NLP (arXiv-only) | `10.48550/arXiv.2005.14165` | arXiv title.search chain |

Five phases:
1. **DOI → OpenAlex resolution** (validates fixes #3 + #7)
2. **Bug-fix validation suite** (#1, #2, #4, #5, #6)
3. **Fresh-paper ingest cycle** — Paper construction with new enum values, chunking, capsule artifacts
4. **Cite-graph by DOI** — end-to-end, no `--openalex-id` flag
5. **RAGResponse + StreamEvent assembly**

---

## ✅ Headline result — the title.search chain works

All three DOIs (including the arXiv-only GPT-3) now resolve to their
canonical OpenAlex Works via the **plain DOI path** — no
`--openalex-id` workaround needed.

| Paper | OpenAlex W-id | OpenAlex `cited_by_count` | Latency |
|---|---|---|---|
| CRISPR-Cas9 | `W2045435533` | 17 089 | 0.79 s |
| LIGO GW150914 | `W2252795400` | 14 109 | 1.58 s |
| GPT-3 | `W3030163527` | 3 029 | 0.68 s |

The GPT-3 resolution exercises the new chain:
- `/works/doi:10.48550/arXiv.2005.14165` → 404
- `export.arxiv.org/api/query?id_list=2005.14165` → "Language Models are Few-Shot Learners"
- OpenAlex `filter=title.search:"Language Models are Few-Shot Learners"` → W3030163527

End-to-end cite-graph hits at the DOI path (Phase 4):

| Paper | Hits | Latency |
|---|---|---|
| CRISPR-Cas9 | **10** | 1.44 s |
| LIGO GW150914 | **10** | 3.14 s |
| GPT-3 | **10** | 1.15 s |

Round-1 audit had this measurement at **0 / 0 / 10** (arXiv DOIs hit
broken fallback, AlphaFold hit primary). The fix is a 3× win on the
arXiv-DOI happy path.

---

## ✅ Bug-fix validation — all 6 fixes pass live

| Fix | Status | Evidence |
|---|---|---|
| #1 `ProvenanceStore.init_db()` standalone | **PASS** | Fresh sqlite + init_db + save + get_for_message round-tripped a record (no SessionStore booted first) |
| #2 `SourceReference.authors: list[str]` | **PASS** | List input `["Jumper", "Evans", "Pritzel"]` accepted; legacy `"Alice, Bob"` coerced to `["Alice", "Bob"]`; `None` → `[]`; `to_citation` yields `[Jumper et al.]` |
| #4 `PaperSource.{OPENALEX,PUBMED,ARXIV,CROSSREF}` | **PASS** | Used in Phase 3 — CRISPR paper ingested with `source=CROSSREF`, LIGO with `OPENALEX`, GPT-3 with `ARXIV`; string round-trip via `PaperSource("openalex")` works for Chroma metadata |
| #5 `BudgetTracker(max_tokens=, max_cost_usd=)` | **PASS** | `BudgetTracker(max_tokens=1000, max_cost_usd=1.0)` constructed; alias propagates to `max_usd`; combined-tokens cap raises `BudgetExceededError` at 1100 > 1000 |
| #6 `KBRouteHit.__iter__` | **PASS** | Direct `name, score = hit` works; full `route_kbs(...)` ranks `crispr_kb` first at score=1.0 for "dual RNA endonuclease genome editing" |
| #3 + #7 arXiv title.search chain (this session) | **PASS** | See headline result above |

---

## ✅ Confirmed-working surfaces (no regressions)

| Surface | Result |
|---|---|
| Live OpenAlex Work metadata fetch | OK — 3/3 papers in < 1.6 s each |
| `chunking_dispatch.chunk_document` on real abstracts | OK — 1 chunk per paper (abstracts < `chunk_size=512`) |
| `capsule_builder.{write_metadata, write_blocks, write_resources}` | OK — 1 block per paper, 0 resources (abstracts have no DOI/GitHub links to mine; expected) |
| `_fetch_seed_work` arXiv chain | OK — GPT-3 arXiv DOI → W3030163527 via title.search |
| `fetch_cited_by_works` | OK — 10 hits per paper at 1.15–3.14 s |
| `SourceReference` with `list[str]` authors | OK — `[Jinek et al., 2012]` citation rendered from list of 6 authors |
| `RAGRequest.kb_names` multi-KB | OK — `["crispr_kb", "biomed_kb"]` accepted |
| `StreamEvent.{status,source,figure_ref,done}` factories | OK — 4 events emitted |
| `KBRouteHit` BM25 routing | OK — query "dual RNA endonuclease genome editing" → crispr_kb wins at score=1.0 |
| `Paper` construction with `source=PaperSource.CROSSREF/OPENALEX/ARXIV` | OK — paper records carry the right source enum |

---

## 🟡 Observations / minor issues

### Round-1 finding #6 (OpenAlex arXiv coverage) — partially better

Round 1 noted OpenAlex's poor coverage of arXiv-only preprints (RAG
paper showed 18 citations vs ~7 000 elsewhere). Round 2 data points:

| Paper | OpenAlex `cited_by_count` | Has DOI in OpenAlex? |
|---|---|---|
| RAG (round 1)         | 18    | **No** |
| Attention (round 1)   | 6 538 | Yes |
| **GPT-3 (round 2)**   | **3 029** | **Yes** (`10.48550/arxiv.2005.14165` lower-cased) |

Some arXiv preprints (GPT-3, Attention) get a DOI back in OpenAlex
metadata even though the lookup-by-DOI primary fails because the
canonical-form DOI in OpenAlex is lowercase. Others (RAG) genuinely
lack the DOI link. The title.search fallback covers both cases.

### `audit_kb/rag/symbols.jsonl` is a dirty working-tree leftover

`tests/audit/results/audit_kb/rag/symbols.jsonl` keeps getting
rewritten by audit-harness runs. Not a bug; just a noisy file in
`git status`. Two options: gitignore the directory, or have the
harness write to a tempdir.

### Audit harness chunks=1 looks misleading

Phase 3 reports `chunks=1` for every paper. That's because OpenAlex
abstracts are 200–1 600 chars (< chunk_size=512). It's not a bug —
the dispatch is honouring config — but a real-paper audit should
also chunk the full text where available to exercise the multi-chunk
path. **Follow-up:** add an "ingest from PDF/HTML" leg to the harness.

---

## 🟢 Net status

- **9 commits on `main` this session** — plan + 6 bug-fix commits +
  arXiv title.search fix + this findings doc.
- **0 new real bugs** surfaced in the second-round audit. Every fix
  from the morning batch is verified live.
- **The arXiv-DOI cite-graph happy path now works end-to-end** — the
  highest-impact win, because it means a user typing
  `--doi 10.48550/arXiv.<id>` gets immediate cite-graph results instead
  of being told to look up the OpenAlex id manually.

---

## Bottom line

Both audit rounds combined: **6 bugs found, 6 bugs fixed, 7 fixes
verified live on real papers from 5 different domains** (protein, ML,
biomedical, physics, NLP). The pipeline is materially more robust than
the morning state. Outstanding follow-ups are scope-limited DX work
(harness gitignore, multi-chunk PDF audit leg, threading explicit
`PaperSource` through remaining ingest adapters) — none are blocking.
