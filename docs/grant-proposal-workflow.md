# Perspicacité for grant proposal writing

Workflow patterns for using Perspicacité as the literature and context
backend when drafting a competitive grant proposal (resubmission or new).

---

## Two-KB strategy

A grant proposal needs two separate knowledge bases with different
retrieval roles:

| KB | Contents | Build command |
|---|---|---|
| `<project>-context` | Prior proposals, reviewer reports, own manuscripts, related grants, call documents | `add_local_papers_to_kb` per file |
| `perspicacite_<zotero-collection>` | Scientific literature seeded from Zotero | `build_kbs_from_zotero` |

Search across both when drafting sections:
```json
{"kb_names": ["myproject-context", "perspicacite_MyCollection"]}
```

Route only the context KB when reviewing internal documents; route only the
literature KB when retrieving citations for claims.

---

## `add_local_papers_to_kb` — contextualizing descriptions

Use `add_local_papers_to_kb` instead of `ingest_local_documents` for any
document that is a proposal, manuscript, reviewer report, or institutional
document. The `abstract` field is **prepended to the full text** before
embedding — it is the first passage hit by semantic search.

Write the `abstract` as a **contextualizing description**, not a
bibliographic summary. Answer: *why does this document matter for this
specific proposal?*

**Template (4–8 sentences):**
```
[What the document is and what it proposes/shows]
[Which tools, methods, or key numbers it contains that are directly relevant]
[Which team members appear and in what role]
[How it connects to other documents in the KB — e.g. "provides the funded backbone", 
 "contains the reviewer criticism addressed in the resubmission"]
[Any key numbers, outcomes, or decisions that matter for drafting]
```

**Example:**
> Funded ANR-SNSF research plan (PI Nothias, started April 2025) providing the
> MetaKH knowledge graph, MetaboT multi-agent system (83.7% annotation accuracy),
> Perspicacité-AI, and Mimosa as operational backbone for the AI-FORGE proposal.
> Directly cited in the proposal as funded infrastructure reducing execution risk
> vs. the 2025 submission. The WPs and timeline here are the reference for
> AI-FORGE WP planning.

---

## Building the context KB: `/build-context-kb` pattern

In the Scriptorium workspace, create `.claude/commands/build-context-kb.md`
with explicit instructions:

1. Call `create_knowledge_base` for the context KB (continue if it already exists)
2. For each document in an explicit file list:
   - Read pages 1–4 (or 1–2 for short call documents)
   - Write a contextualizing description (see above)
   - Call `add_local_papers_to_kb` with `kb_name`, `title`, `authors`, `year`,
     `file` (absolute path), and the description as `abstract`
   - Log chunks added or error
3. Report a summary table: file | title | status | chunks
4. Call `build_kbs_from_zotero` to populate the literature KB

Keep the file list explicit and versioned in the command file — it is
your audit trail for what context was available at drafting time.

---

## Reviewer report ingestion

The prior-cycle evaluation report is the highest-value document in the
context KB for a resubmission. Ingest it with a description that names
each criticism explicitly:

```python
{
  "title": "ANR-NRF 2025 AI-FORGE — Reviewer Evaluation Report",
  "abstract": (
    "2025 ANR-NRF committee report on AI-FORGE (Nothias, Kang, Kim, Libis). "
    "Overall positive assessment; two specific criticisms: (1) BGC selection "
    "rationale needs more detail; (2) bacterial vs. fungal sub-pipelines need "
    "clearer architectural separation. Teams, budget balance, risk management, "
    "and impact all positively assessed. Key input for 2026 resubmission."
  )
}
```

Then retrieve it with `get_relevant_passages` when drafting the sections
that address the criticisms.

---

## `push_notes_to_zotero` — archiving annotations

After generating a RAG summary or writing a claim annotation with
Perspicacité, push it back to the Zotero record so it survives outside
the KB and is visible to collaborators in the shared library:

```json
{
  "notes": [{
    "doi": "10.1145/3731443.3771350",
    "content": "Q2Forge — Best Paper RAGE-KG/ISWC 2025.\nKey evidence for AI-FORGE WP2 KG QA claim.\nSPARQL generation accuracy: see Table 2.",
    "tags": ["ai-forge-2026", "wp2", "claim-C3"]
  }]
}
```

Use `item_key` instead of `doi` when you already have the Zotero key from
a prior `push_to_zotero` call — avoids a library search round-trip.

---

## Local Zotero write (personal library, no cloud key)

If your personal Zotero library is configured with a local desktop base URL,
both `push_to_zotero` and `push_notes_to_zotero` will write directly to the
running Zotero desktop app without needing a cloud API key:

```yaml
# config.yml
zotero:
  base_url: "http://localhost:23119/api"
  library_id: "YOUR_USER_ID"
  library_type: "user"
  # api_key not required for personal library via local API
```

Group / shared libraries always require a cloud API key regardless of
`base_url` setting (group writes go through Zotero's cloud sync layer).

---

## Suggested `generate_report` queries for grant drafting

| Purpose | Query | Mode | KBs |
|---|---|---|---|
| State of the art for Section 1 | `"what is the current state of AI for metabolomics annotation and what are the gaps?"` | `advanced` | literature |
| Position own tools | `"what tools exist for knowledge graph QA and automated SPARQL generation?"` | `advanced` | both |
| Reviewer criticism answer | `"bacterial vs fungal BGC expression systems and selection criteria"` | `profound` | literature |
| Impact framing | `"applications of multi-agent AI in biomedical research funding landscape 2024-2026"` | `literature_survey` | literature |

---

## Quick-reference: grant proposal MCP calls

```
# Build context KB (run once, re-run after adding new documents)
perspicacite:create_knowledge_base { "name": "myproject-context", ... }
perspicacite:add_local_papers_to_kb { "kb_name": "myproject-context", "papers": [...] }

# Build literature KB from Zotero
perspicacite:build_kbs_from_zotero { "library_id": "YOUR_LIBRARY_ID" }

# Search during drafting
perspicacite:search_by_passage { "kb_names": ["myproject-context", "perspicacite_MyCol"], ... }
perspicacite:get_relevant_passages { "kb_names": [...], "query": "...", "adaptive": true }

# Generate section draft support
perspicacite:generate_report { "query": "...", "kb_names": [...], "mode": "advanced" }

# Archive annotation back to Zotero
perspicacite:push_notes_to_zotero { "notes": [{ "doi": "...", "content": "...", "tags": [...] }] }
```
