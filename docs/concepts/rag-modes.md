# RAG Modes

Perspicacité offers six RAG modes that trade off cost, depth, and latency against each
other. Choosing the right mode for your question type is the single biggest lever on
answer quality.

---

## Overview

| Mode | CLI value | Description | Best for | Typical LLM calls |
|------|-----------|-------------|----------|-------------------|
| Basic | `basic` | Single hybrid retrieval + synthesis | Well-curated KB, quick factual questions | 1 |
| Advanced | `advanced` | Query expansion + WRRF fusion + reranking | Broader KB search, precision over recall | 2-3 |
| Profound | `profound` | Multi-cycle (up to 3 iterations) with self-evaluation | Complex questions, contradictory evidence | 3-5 |
| Agentic | `agentic` | Intent-based agent with tool use, up to 5 iterations | Questions needing live discovery beyond the KB | 5-15 |
| Literature Survey | `literature_survey` | Broad search, theme clustering, AI recommendations | Mapping a research field, exploring a new topic | 10-50+ |
| Contradiction | `contradiction` | Multi-paper claim clustering into agreement / disagreement / open | Comparing conflicting findings | 3-8 |

---

## Basic

Basic mode runs a single hybrid BM25 + vector retrieval pass against the selected KB,
retrieves the top-K chunks (default: `knowledge_base.default_top_k = 10`), and
synthesizes an answer in one LLM call.

Use this when:
- Your KB is well-curated and focused on the topic
- You want a fast answer and the question is specific
- You are running a high-volume pipeline and cost matters

Do not use when:
- The question requires synthesizing across many loosely related papers
- The KB is broad and the question would benefit from query expansion
- You need to find papers that are topically adjacent but not obviously related

---

## Advanced

Advanced mode applies query expansion before retrieval: the original query is
rephrased into multiple sub-queries that target different aspects of the topic.
Results from each sub-query are merged using Weighted Reciprocal Rank Fusion (WRRF)
and re-ranked before synthesis. This improves precision for broad KBs where a single
query vector may not surface the most relevant papers.

Use this when:
- Your KB contains more than ~50 papers on a broad topic
- The question has multiple facets (e.g., "what are the effects of X on Y in the
  context of Z?")
- Basic mode returns obviously relevant papers alongside many off-topic ones

---

## Profound

Profound mode runs up to 3 retrieval-synthesis cycles. After each cycle it evaluates
the partial answer against the original question, identifies gaps, and generates
follow-up queries to fill them. Each iteration's retrieved chunks are accumulated and
the final synthesis is over the full accumulated context.

Use this when:
- The question is complex and no single retrieval pass covers it adequately
- You expect the answer to require evidence from multiple sub-topics
- You have budget for 3-5 LLM calls per answer

The multi-cycle approach is particularly effective for questions like "compare
approach A and approach B across these three dimensions" where each dimension may
require a separate retrieval pass.

---

## Agentic

Agentic mode uses an intent-based agent that can call tools: it can run KB searches,
download full texts for papers it finds interesting, and (if SciLEx is installed)
search the live academic databases. The agent runs up to 5 tool-use cycles.

Use this when:
- The question requires papers outside the current KB
- You want the system to autonomously expand its evidence base
- You are comfortable with variable cost (5-15 LLM calls per answer)

Note: agentic mode works best with direct LLM API access (Anthropic, OpenAI). With
Ollama or agent-CLI routing, tool-use reliability degrades — stick to basic / advanced
/ profound modes for local-model deployments.

---

## Literature Survey

Literature Survey is designed for systematic field mapping. It runs a broad multi-pass
search, clusters the retrieved papers by theme, asks the LLM to recommend which
clusters are most central to the topic, and generates a structured survey report.

Use this when:
- You are entering a new research area and want a landscape overview
- You need to identify the key themes and open questions in a field
- You are willing to wait several minutes and spend 10-50+ LLM calls

Literature Survey supports **checkpoint/resume**: the intermediate state (retrieved
papers, clustered themes) is persisted to SQLite and can be resumed if the run is
interrupted. See `GET /api/survey/{session_id}` for status polling.

---

## Contradiction

Contradiction mode retrieves papers related to the question, extracts specific claims,
and groups them into three buckets: agreement, disagreement, and open questions. The
output is a structured view of the evidential landscape rather than a narrative answer.

Use this when:
- You are comparing conflicting findings across papers (e.g., meta-analytic questions)
- You want to understand which claims have strong consensus vs. which are contested
- You are doing a systematic review and need to map the disagreement space

---

## Selecting a mode

### CLI

```bash
perspicacite -c config.yml query "your question" --kb my-kb --mode basic
# modes: basic | advanced | profound | contradiction
```

(Literature Survey and Agentic modes are available via the REST API and web UI; the
CLI `query` command covers basic, advanced, profound, and contradiction.)

### REST API

```bash
curl -sN -X POST http://localhost:5468/api/chat \
  -H "Content-Type: application/json" \
  -d '{"query": "your question", "kb_name": "my-kb", "mode": "literature_survey", "stream": true}'
```

Mode values: `basic`, `advanced`, `profound`, `agentic`, `literature_survey`, `contradiction`.

### MCP

Use the `generate_report` tool, which accepts the same mode strings.

---

## LLM tiering across modes

Each mode uses different LLM stages that can be routed to different models:

```yaml
llm:
  models:
    routing:    "claude-haiku-4-5"   # KB router (auto mode)
    screening:  "claude-haiku-4-5"   # paper relevance screen
    rephrase:   "claude-haiku-4-5"   # query rephrasing (advanced / profound)
    contextual: "claude-haiku-4-5"   # contextual retrieval prefix generation
  default_model: "claude-sonnet-4-5" # synthesis (all modes)
```

Leaving `models` empty routes all stages to `default_model` — the safe default.

---

## Related topics

- [concepts/knowledge-bases.md](knowledge-bases.md) — selecting and routing KBs
- [concepts/provenance.md](provenance.md) — how the retrieval trace is recorded per mode
- [reference/config.md](../reference/config.md) — `rag_modes.*` config keys
