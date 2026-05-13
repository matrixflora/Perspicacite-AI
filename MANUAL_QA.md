# Manual QA Checklist — v2.x Multi-Feature Expansion (Phase 5)

Start the server:

```bash
uv run perspicacite -c config.yml serve
```

Open the UI at http://localhost:8000 and hard-refresh (Cmd+Shift+R) to bypass cached static assets.

---

## Phase 5 UI — Feature 1: KB Statistics Panel

- [ ] Select a non-empty knowledge base from the KB dropdown in the sidebar.
- [ ] A small tab strip ("Info" / "Stats") appears below the KB selector.
- [ ] Click **Stats** — the panel below the tabs loads and shows:
  - A header row with the KB name, paper count, chunk count, and embedding model.
  - An inline SVG bar chart of papers by year (bars are proportional; year labels visible when there is room).
  - A "By Source" table.
  - A "By Content Type" table (with coloured pipeline badges in the Type column).
  - A "Top Journals" table (up to 10 rows).
- [ ] Click **Info** — the tab returns to the plain `{name} / {n} papers, {m} chunks` view.
- [ ] With no KB selected, the tab strip is hidden.
- [ ] If the stats endpoint returns an error, an error message is shown (not a crash).

---

## Phase 5 UI — Feature 2: Paper Detail Panel + Pipeline-Step Badges

- [ ] Run any query that returns sources (Basic, Advanced, Profound, or Agentic mode).
- [ ] Each source row in the thinking panel shows a coloured badge:
  - Green "Structured" — paper has structured full text (JATS/HTML sections).
  - Blue "Full text" — paper has PDF-extracted full text.
  - Amber "Abstract" — only abstract available.
  - Grey "—" — no content retrieved.
- [ ] If a source has a DOI, a small "details" link appears next to the badge.
- [ ] Click **details** — a slide-over panel opens on the right side of the screen showing:
  - Content-type badge at the top.
  - Title (bold), authors, year · journal, DOI link.
  - Abstract (if available).
  - Reference count (if available).
- [ ] Click the × button — the panel closes cleanly.
- [ ] For a source with no DOI, clicking "details" shows a "No DOI available" message (not a crash).
- [ ] Multi-KB sources show a purple KB tag beside the badge if `kb_name` is present in the SSE event.

---

## Phase 5 UI — Feature 3a: Conversation Search + Export

- [ ] The Chat History sidebar now has a search box at the top ("Search conversations…").
- [ ] Typing a term (wait ~250 ms) shows matching conversations with title + snippet below the box.
- [ ] Clicking a search result loads that conversation in the main chat area and clears the search box.
- [ ] Pressing backspace to empty the box hides the results dropdown.
- [ ] Each conversation item in the history list has a small export button (⤓).
  - Clicking it triggers a browser download of a `.md` file for that conversation.
  - (No export button / no error for conversations with no `convId`.)

---

## Phase 5 UI — Feature 3b: Mode Picker — Contradiction

- [ ] The mode dropdown includes **⚖️ Contradiction** as an option.
- [ ] Selecting it sets the mode; the POST body sent to `/api/chat` includes `"mode": "contradiction"`.
- [ ] Running it on a KB with 3+ thematically related papers returns a structured answer covering consensus, disagreement, and open questions (or a graceful fallback if the backend does not yet implement a dedicated handler).
- [ ] Running it on a tiny/empty KB does not crash the UI — it degrades to a plain answer.

---

## Phase 5 UI — Feature 3c: Advanced Query Options

- [ ] Below the chat textarea, a collapsed **"Advanced options"** disclosure block is visible.
- [ ] Opening it reveals three controls:
  1. **Vector vs BM25 weight** slider (0–1). The label updates live showing `vector X.XX / BM25 X.XX`.
  2. **Recency weight** slider (0–1, default 0). The label updates live.
  3. **Knowledge Bases** checkbox list (populated from the KB selector; one checkbox per KB).
- [ ] With the disclosure **closed** (default state), sending a query uses only the standard `kb_name` / no extra weight fields — behaviour is identical to before.
- [ ] Open the disclosure, move the vector/BM25 slider away from 0.5, then send a query. The POST body (visible in browser DevTools → Network → `/api/chat` → Request Payload) includes `vector_weight` and `bm25_weight`.
- [ ] Set recency weight > 0; confirm `recency_weight` is included in the POST body.
- [ ] Check 2+ KB boxes; confirm `kb_names` is included (and `kb_name` is omitted/undefined) in the POST body.
- [ ] With all sliders at default and 0 or 1 KB checked, no extra fields are sent.

---

## Provenance UI (Phase 2)

- [ ] After asking a question, the assistant message shows a "Provenance" disclosure.
- [ ] Expanding it shows Request (mode/kb/top_k/recency/weights), Retrieval (rank/score/KB/type/source), and Reasoning & LLM calls sections.
- [ ] LLM-call rows expand to show full prompt messages + response text.
- [ ] The conversation header shows a "RO-Crate bundle" link whenever a conversation is loaded.
- [ ] Clicking "RO-Crate bundle" downloads a .zip containing ro-crate-metadata.json, conversation.md, provenance/, sources.json.
- [ ] The provenance disclosure is absent for conversations loaded from history (no message_id available) — no JS error in console.
- [ ] The "RO-Crate bundle" link is hidden when no conversation is active (new chat).

---

## Backend Smoke Tests (Optional)

These do not require the web UI.

- [ ] `uv run perspicacite -c config.yml screen-papers --input a.bib --candidates b.bib --output out.bib --threshold 0.0`
- [ ] `uv run perspicacite -c config.yml pubmed-search "crispr" --max 3 --email you@example.org`
- [ ] `POST /api/kb/<name>/dois` with `{"dois": ["10.1101/2021.01.01.425001"]}` successfully adds a bioRxiv preprint (returns `added_papers: 1`).
- [ ] `GET /api/kb/<name>/stats` returns JSON with `paper_count`, `chunk_count`, `by_year`, `by_source`, `by_content_type`, `top_journals`, `embedding_model`.
- [ ] `GET /api/paper?doi=10.1016/j.cell.2021.01.001` returns paper metadata (or `{error: ...}` — not a 500).
- [ ] `GET /api/conversations/search?q=crispr` returns `{results: [...]}`.
- [ ] `GET /api/conversations/<id>/export?format=markdown` returns a downloadable `.md` file with conversation content.

---

## Async ingestion progress (Phase 4)

- [ ] Creating a KB from a BibTeX file shows a progress bar that fills from 0 → 100% with a label like "12/34 · embedded".
- [ ] Adding a list of DOIs to an existing KB shows the same progress bar.
- [ ] On completion, the label shows "Done · N papers, M chunks" and the KB stats refresh.
- [ ] If the SSE stream drops, the bar continues updating via polling every 2 s.

---

## Zotero push (Phase 5)

- [ ] With `zotero.enabled: true` and valid credentials in `config.yml`, opening a paper-detail slide-over shows a "Send to Zotero" button.
- [ ] With `zotero.enabled: false`, the button is hidden.
- [ ] Clicking the button POSTs the current DOI to `/api/zotero/push` and shows an alert with the returned key (or failure reason).

## Multi-KB chat across all six modes (2026-05-13, cycle 3)

For each of `basic`, `advanced`, `profound`, `contradiction`, `literature_survey`, `agentic`:
1. Open the chat panel, multi-select two KBs that share an embedding model.
2. Enter a representative query.
3. Confirm the answer streams to completion (no error event).
4. Confirm source cards show `kb_name` tags from both KBs (visible in the source-card chip).
5. Confirm provenance JSONL contains a `kb_names` field reflecting the selection.

Embedding-mismatch test:
- Multi-select two KBs with different embedding models.
- Confirm chat surfaces a clear error (no silent fallback).

Notes:
- `literature_survey` doesn't retrieve from any KB; multi-KB selection is honored as a *storage* target (papers are stored into `kb_names[0]`). Log line `survey_multi_kb_storage` appears when >1 KB is selected.
- `agentic`'s KB_SEARCH step builds a `MultiKBRetriever` automatically when the request carries multi-KB; per-paper `kb_name` propagates through `SourceReference`.

## Zotero → KB ingest (2026-05-13, cycle 3)

Prereqs: set `zotero.enabled: true`, `zotero.api_key`, `zotero.library_id` in `config.yml`.

1. Click "Build KBs from Zotero" in the KB panel header.
2. Confirm modal loads a plan table with rows per top-level collection + (optional) "Unfiled".
3. Rename one target KB; uncheck another row.
4. Click Execute. Confirm a progress pane appears with per-item lines.
5. After "Done", confirm new KBs appear in the KB list with non-zero paper/chunk counts.
6. Verify DOI dedup: re-run the same plan. Expect "skipped" progress events; no duplicate items added.

503 / disabled path:
- With `zotero.enabled: false`, the modal loading text says "Zotero is not configured (set zotero.enabled in config.yml)."

MCP path:
- Call `build_kbs_from_zotero(plan_only=True)` from an MCP client; confirm `plan` is returned.
- Call with `plan_only=False`; confirm `per_kb` summary is returned.
- Call with `zotero.enabled=false`; confirm `{"error": ...}` is returned (no crash).

## Local documents → KB (2026-05-13, cycle 3)

Web upload:
1. Open KB detail.
2. Drag a PDF, a markdown file, and a Python file onto the drop zone.
3. Confirm per-file progress lines stream in.
4. After "Done", confirm chunk count went up.
5. Run a chat query that should hit the markdown file; confirm a chunk with `heading_path` appears in sources.

CLI:
- `uv run perspicacite ingest-local --kb mykb --path /abs/path/to/file.md`
- Confirm exit code 0 and `Done: {...}` output.

Server-side path:
- Without `local_docs.allowed_roots` set, `POST /api/kb/mykb/local-paths` returns 503.
- With one root set, posting a path under it returns a `job_id`; posting `/etc/hosts` returns 400.

MCP:
- `ingest_local_documents(kb_name="mykb", paths=["/etc/hosts"])` → `{"error": "..."}` when no allow-list.

Language tags in provenance:
- Open a conversation that retrieved a code chunk.
- Open the provenance JSONL sidecar; confirm the chunk row carries `language` and `content_type`.

## Capsule build (2026-05-13, cycle A)

Auto-build on ingest:
1. Ingest a paper via BibTeX, DOIs, local PDF, or Zotero with `capsule.auto_build_on_ingest: true`.
2. Confirm `<data_root>/capsules/<paper_id>/metadata.json` exists with `"producer": "perspicacite"` and `"capsule_version": "0.1"`.
3. Confirm `figures/index.json` exists (empty list for paths without a tracked PDF — only the local-PDF ingest extracts figures in Cycle A).
4. Confirm `text/blocks.jsonl` has one row per paragraph with a `section` field (or `full_text` fallback).
5. Confirm `resources.json` has entries if the paper mentions DOIs / GitHub / Zenodo / PRIDE / GEO / etc.

Retro-build (full capsule with chunks gaining provenance):
- CLI: `uv run perspicacite build-capsules --kb mykb` — confirm one line per paper and a summary.
- MCP: `build_capsules_for_kb(kb_name="mykb")` — confirm `per_paper` summary.
- UI: KB panel → "Build capsules" button → confirm SSE progress stream renders one event per paper, ending with "Done".

Idempotency:
- Re-run the same retro-build with no `--force`; confirm every paper's status is `skipped`.
- Re-run with `--force`; confirm every paper's status is `built` again.

Provenance:
- After retro-build, inspect the provenance JSONL sidecar; confirm chunks now carry `source_section`, `char_span`, and `figure_refs`/`resource_refs` when applicable.
