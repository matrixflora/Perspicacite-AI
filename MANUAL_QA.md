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
