# Provenance

Every answer Perspicacité produces carries a retrieval trace recording how the answer
was constructed. This document explains what provenance data is collected, where it
lives, how to query it, and how to export it for reproducible research workflows.

---

## What is recorded

For every message that triggers a RAG synthesis, Perspicacité records:

- **Retrieved chunks** — the chunk IDs, paper IDs, KB names, similarity scores, and
  BM25 scores for every chunk that was considered during retrieval
- **RAG mode** — `basic`, `advanced`, `profound`, `agentic`, `literature_survey`, or
  `contradiction`
- **Model** — the LLM provider and model name used for synthesis
- **Latency** — wall-clock milliseconds from request receipt to response completion
- **Conversation context** — the conversation ID and message ID linking provenance to
  the specific turn it describes
- **Timestamp** — ISO 8601 timestamp of the retrieval event

This data is stored in SQLite alongside the conversation history.

---

## Querying provenance

### Per-message provenance

```bash
curl http://localhost:5468/api/conversations/{conv_id}/messages/{msg_id}/provenance
```

Returns a JSON object with the retrieved chunks, model, mode, and latency for that
specific message.

### All provenance for a conversation

```bash
curl http://localhost:5468/api/conversations/{conv_id}/provenance
```

Returns an array of provenance records, one per synthesized message in the
conversation.

---

## Exporting provenance

### Markdown export

```bash
curl "http://localhost:5468/api/conversations/{conv_id}/export?format=markdown" \
  -o conversation.md
```

Exports the conversation as a Markdown document with inline citations and a references
section at the bottom listing the papers retrieved.

### RO-Crate export

```bash
curl "http://localhost:5468/api/conversations/{conv_id}/export?format=ro-crate" \
  -o provenance_bundle.zip
```

Exports the conversation plus its complete provenance as an
**RO-Crate 1.1** zip bundle. The bundle contains:

```
ro-crate-metadata.json   # RO-Crate manifest with @context and @graph
conversation.json        # full conversation with message-level provenance
papers/                  # BibTeX entries for all retrieved papers
chunks/                  # text of retrieved chunks, one file per chunk
```

RO-Crate is the Research Object format used by reproducible science workflows and is
supported by tools like WorkflowHub and Zenodo. The metadata graph records the
retrieval event as a `CreateAction` with `instrument` pointing to the RAG mode and
model, and `result` pointing to the synthesized message.

---

## What provenance does not capture

- **LLM intermediate reasoning** — the chain-of-thought within a single synthesis
  call is not stored. The retrieved chunks and the final answer are; the internal
  reasoning steps are not.
- **SciLEx search queries** — for agentic mode, the live database queries fired during
  the run are logged to stderr but not persisted in the provenance store.
- **Streaming deltas** — SSE streaming sends answer tokens as they arrive; the
  provenance record is written after synthesis completes.

---

## Relationship to PaperSource

Each retrieved paper in the provenance trace carries its `source` field from the
`PaperSource` enum, recording which database the paper was originally discovered from:
`OPENALEX`, `PUBMED`, `ARXIV`, `CROSSREF`, `SEMANTIC_SCHOLAR`, `BIBTEX`, `LOCAL`, etc.
This makes the provenance trace not only reproducible within Perspicacité but also
auditable back to the original source database.

See [reference/paper-source-enum.md](../reference/paper-source-enum.md) for the full
enum and how each value is assigned.

---

## Related topics

- [concepts/rag-modes.md](rag-modes.md) — how different modes affect what provenance
  is recorded
- [reference/rest-api.md](../reference/rest-api.md) — provenance and export endpoints
- [reference/paper-source-enum.md](../reference/paper-source-enum.md) — PaperSource
  enum values
