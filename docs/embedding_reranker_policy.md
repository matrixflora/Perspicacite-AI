# Embedding Provider × Reranker Policy

**Status:** active guidance for choosing embeddings, providers, and reranking.
**Last validated:** 2026-05-28 (Perspicacité-eval SciFact/NFCorpus + Qwen3 reranker probe).

This document answers three practical questions:

1. Which embedding model / provider should a KB use?
2. When should the cross-encoder reranker be ON vs OFF?
3. Why the two choices are coupled, and what the KB-compatibility constraint is.

---

## 1. TL;DR decision table

| KB content | Recommended embedding | Provider | Reranker | Hybrid (BM25) |
|------------|----------------------|----------|----------|---------------|
| **Biomedical / scientific (default)** | `all-MiniLM-L6-v2` | local | **ON** (ms-marco) | **ON** |
| Biomedical, higher recall | `BAAI/bge-m3` | local | ON (bge-reranker-v2-m3) | ON |
| Code | `openrouter/mistralai/codestral-embed-2505` | OpenRouter | **OFF** | **OFF** |
| Hard / asymmetric retrieval | `openrouter/qwen/qwen3-embedding-8b` | OpenRouter | **OFF** | **OFF** |
| General, hosted | `openrouter/openai/text-embedding-3-large` | OpenRouter | **OFF** | OFF |

**The rule in one line:** *both the reranker and the BM25 hybrid only help when they
add signal the embedder lacks. Weak local embedder → rerank + hybrid ON. Strong
instruction-tuned embedder → both OFF (trust the embedder).* See §3 (reranker) and
§4 (hybrid) for the measured evidence.

---

## 2. Using OpenRouter for embeddings

OpenRouter exposes an OpenAI-compatible `/embeddings` endpoint. Perspicacité reaches
it through the existing `LiteLLMEmbeddingProvider` — no code change needed, just a
model string with the `openrouter/` prefix and an `OPENROUTER_API_KEY` in `.env`
(the same key already used for LLM routing when `default_provider: openrouter`).

```yaml
# config.yml — a code knowledge base
knowledge_base:
  embedding_model: "openrouter/mistralai/codestral-embed-2505"   # 1536-dim, code-specialised
  similarity_threshold: 0.0          # API embeddings score lower than MiniLM; don't over-filter

rag_modes:
  reranker_enabled: false            # strong embedder → no cross-encoder (see §3)
```

Verified working via litellm on 2026-05-28 (`openrouter/` route, dims confirmed):

| Model | Dim | Note |
|-------|-----|------|
| `openrouter/mistralai/codestral-embed-2505` | 1536 | Mistral code embedding — best for code KBs |
| `openrouter/qwen/qwen3-embedding-8b` | 4096 | strongest general retriever we measured |
| `openrouter/qwen/qwen3-embedding-4b` | 2560 | smaller Qwen3 |
| `openrouter/baai/bge-m3` | 1024 | multilingual |
| `openrouter/openai/text-embedding-3-large` | 3072 | hosted general |
| `openrouter/google/gemini-embedding-001` | 3072 | hosted general |

Dimensions are registered in `LiteLLMEmbeddingProvider._get_dimension()`; add new
slugs there if you use a model not in the list (it defaults to 1536 otherwise).

> **Caution — local fallback dimension mismatch.** `create_embedding_provider(..., use_local_fallback=True)`
> wraps API embeddings with a local `all-MiniLM-L6-v2` (384-dim) fallback. If the
> OpenRouter call fails mid-ingest, the fallback emits 384-dim vectors that are
> incompatible with the KB's real dimension. For production API-embedding KBs,
> prefer a config that does not silently fall back, or monitor
> `fallback_triggered_count`.

---

## 3. The reranker switch (`reranker_enabled`)

Perspicacité's cross-encoder reranker runs on **web-search results** (the
`resolve_papers` enrich→rerank pipeline) and on **agentic relevance scoring** — it
does **not** rerank KB vector retrieval (KB retrieval is pure two-pass vector). The
new `rag_modes.reranker_enabled` flag (default `True`) is the master switch; setting
it `False` (or leaving `reranker_model` empty) disables reranking everywhere and
skips the boot-time model prewarm.

### Why disable it for strong embedders — the data

SciFact dev (n=188), Qwen3-Embedding-8B first-stage retrieval, then rerank the
top-20 with a cross-encoder (`perspicacite-eval/scripts/qads_qwen3_rerank_probe.py`):

| First stage | + ms-marco CE | + bge-reranker-v2-m3 |
|-------------|---------------|----------------------|
| full (NDCG@10 0.906) | 0.892 (**−1.4 pp**) | 0.896 (**−1.0 pp**) |
| QADS topk_1024 (0.944) | 0.897 (**−4.7 pp**) | 0.901 (**−4.3 pp**) |

**Both a weak (ms-marco) and a strong (bge-reranker-v2-m3) cross-encoder DEGRADE a
SOTA instruction-tuned embedder.** Qwen3-8B already puts the gold doc in the top-5
~97–99% of the time (R@5 ≈ 0.97–0.99); a general cross-encoder reorders that
near-perfect list and demotes correct hits. The reranker is simply a worse relevance
judge than the embedder for this domain.

Contrast — `all-MiniLM-L6-v2` on SciFact: the ms-marco cross-encoder adds **+10.6 pp**
NDCG@10 (0.745 → 0.851). A weak bi-encoder leaves real headroom for a cross-encoder.

### Rule of thumb

- **Local weak embedder** (MiniLM 384-dim) → `reranker_enabled: true`.
- **Strong instruction-tuned / hosted embedder** (Qwen3, codestral-embed,
  text-embedding-3-large, bge-m3 at full strength) → `reranker_enabled: false`.
- On topically-uniform corpora (e.g. NFCorpus medical topics) the cross-encoder is a
  no-op even for MiniLM — disabling it costs nothing and saves latency.

---

## 4. Hybrid retrieval (BM25 + vector) — same axis as the reranker

BM25 lexical scoring is blended with vector scores when `use_hybrid: true`
(per-mode in `rag_modes`). Like the reranker, **its value shrinks as the
embedder strengthens** — and a high BM25 weight actively hurts a strong embedder.

Measured (`perspicacite-eval/scripts/qads_qwen3_hybrid_probe.py`,
`score = α·vector + (1−α)·BM25`, SciFact dev n=188):

| Embedder | pure vector | light BM25 (α≈0.75) | balanced (α=0.5) | BM25-heavy (α=0.25) | pure BM25 |
|----------|-------------|---------------------|------------------|---------------------|-----------|
| **Qwen3-8B** (strong) | 0.906 | **0.912** (+0.6) | 0.892 (−1.4) | 0.869 (−3.7) | 0.820 (−8.6) |
| **all-MiniLM** (weak) | 0.745 | — | **0.774** (+2.9) | — | — |

- **Weak embedder (MiniLM):** a balanced hybrid adds ~+3 pp. Keep `use_hybrid: true`.
- **Strong embedder (Qwen3, codestral, OpenAI-3-large):** only a *light* BM25 touch
  is marginally positive; balanced weights HURT. Because `advanced` mode lets the LLM
  pick the weight (it has chosen 0.5–0.6, which lands in the harmful zone — this is the
  root cause of the SciFact claim-431 miss), the safe choice is **`use_hybrid: false`**.
  The +0.6 pp upside of perfectly-tuned light BM25 is not worth the −3.7 pp downside
  risk of LLM mis-weighting.

So the rule generalises across all three stages — **reranker, hybrid/BM25 weight,
and QADS dimension masking all want: weak embedder → add the auxiliary signal;
strong embedder → trust the embedder.**

| Stage | Weak embedder (MiniLM) | Strong embedder (Qwen3/codestral/OpenAI) |
|-------|------------------------|------------------------------------------|
| Cross-encoder reranker | ON (+~10 pp on SciFact) | OFF (−1 to −5 pp) |
| Hybrid BM25 | ON, balanced (+~3 pp) | OFF / very light only |
| QADS topk dim mask | n/a (low-dim) | helps (+3.8 pp at top-1024 of 4096) |

## 6. KB compatibility constraint (why you can't swap embeddings per query)

The embedding model is **baked into the KB at ingest time**: ChromaDB stores vectors
of a fixed dimension produced by one model. A query **must** be embedded by the *same*
model, or cosine search is meaningless (dimension mismatch → silent 0 recall).

Consequences for agentic / MCP routing:

- An agent **cannot** freely pick an embedding model per query against one KB.
- `MultiKBRetriever.check_embedding_compat()` already refuses to fan a query across
  KBs that were embedded with different models — respect that error rather than
  working around it.
- What an agent **can** select per query: the **reranker** (text-based, KB-agnostic),
  the **RAG mode** (basic/advanced/…), and **which KB to route to** (if several exist).

### Recommended topology for mixed domains

Maintain one KB per (domain, embedding) pair and route by domain:

```
kb_biomed   → embedding: all-MiniLM-L6-v2          reranker: ON
kb_code     → embedding: codestral-embed-2505      reranker: OFF
kb_general  → embedding: text-embedding-3-large    reranker: OFF
```

Each KB advertises its embedding model in `kb_metadata`; the skill/MCP layer picks
the KB (and therefore the embedding) by the query's domain, and sets the reranker
per the §3 rule. Embedding choice is an **ingest-time, per-KB** decision; reranker is
the **cheap per-query knob**.

### How an agent discovers compatibility (MCP)

`list_knowledge_bases` returns, per KB: `embedding_model` and a `retrieval_hint`
(`embedding_strength`, `recommended_reranker`, `recommended_hybrid`). The agent
workflow:

1. `list_knowledge_bases` → see each KB's `embedding_model` + hint.
2. `route_kbs(query)` → rank KBs by relevance to the query.
3. Group the chosen KBs **by identical `embedding_model`** before passing as
   `kb_names` (mixed-embedding groups are rejected by `check_embedding_compat`).
4. Set reranker/hybrid from the `retrieval_hint`.

> **Single-server caveat:** one running server embeds queries with *one* model
> (`config.knowledge_base.embedding_model`) and `reranker_enabled`/`use_hybrid`
> are server-level. To serve multiple embedding families, run one server per
> profile (e.g. MiniLM:8000, codestral:8003, OpenAI:8002) and route to the right
> *server*. Per-request reranker/hybrid override is a known future enhancement
> (see §7 gaps).

---

## 7. Pointers

- Config field: `RAGModesConfig.reranker_enabled` (`src/perspicacite/config/schema.py`).
- Gate sites: `rag/resolve_papers.py` (web-search rerank), `web/state.py` (prewarm),
  `rag/agentic/orchestrator.py` (relevance rerank).
- Embedding dims: `llm/embeddings.py` `LiteLLMEmbeddingProvider._get_dimension()`.
- Evidence: `perspicacite-eval/docs/findings_2026_05_27.md` (Qwen3 QADS + reranker
  appendix), `perspicacite-eval/docs/sota_benchmarks_2026_05_27.md`.
- Tests: `tests/unit/test_reranker_enabled_flag.py`.
