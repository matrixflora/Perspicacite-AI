# Ingest a BibTeX File

This guide covers how to import a `.bib` file into a Perspicacité knowledge base,
what happens during ingestion, and how to handle common issues.

---

## Prerequisites

- Perspicacité installed and configured (`config.yml` with an LLM key and
  `pdf_download.unpaywall_email`)
- A `.bib` file in standard BibTeX format

No running server is required for `create-kb` — it runs as a standalone command.

---

## Basic import

```bash
perspicacite -c config.yml create-kb my-kb --from-bibtex refs.bib
```

With a description:

```bash
perspicacite -c config.yml create-kb my-kb \
  --from-bibtex refs.bib \
  --description "Diamond magnetometry papers 2020-2026"
```

If the KB name is already taken, the command exits with an error. To add papers to an
existing KB from a BibTeX file, use `add-to-kb`:

```bash
perspicacite -c config.yml add-to-kb my-kb --from-bibtex more-refs.bib
```

---

## What happens during ingest

For each entry in the `.bib` file, the pipeline runs:

1. **Parse** — extract title, authors, year, DOI, PMID, URL, abstract from the BibTeX
   entry using `bibtexparser`.
2. **Discover** — look up the DOI against OpenAlex and Unpaywall to get the PMCID,
   arXiv ID, open-access status, and publisher-PDF link.
3. **Fetch full text** — try the content pipeline in priority order:
   - PMC JATS XML (structured sections + references)
   - arXiv HTML
   - OA PDF via Unpaywall
   - Publisher PDF via publisher-specific API (ACS, Springer, Wiley, Elsevier)
   - Institutional-access PDF if browser cookies are configured
   - Abstract only (from Crossref / OpenAlex metadata)
4. **Cache PDF** — write the PDF bytes to `data/papers/<doi>.pdf` and a `.meta.json`
   sidecar (when `pdf_download.cache_pdfs: true`, the default).
5. **Chunk** — split the text into chunks according to the configured strategy (default:
   token-based, `chunk_size: 1000`, `chunk_overlap: 200`).
6. **Embed** — compute chunk embeddings using the configured model
   (`knowledge_base.embedding_model`, default `text-embedding-3-small`).
7. **Index** — write chunks and embeddings to the ChromaDB collection; write paper
   metadata to SQLite.

Papers without a DOI skip steps 2-4 but are still indexed on their abstract text if
present. Papers where no content is retrievable are recorded in the SQLite metadata
table with `content_type: "none"` and are not embedded.

---

## Adding papers to an existing KB

```bash
# Via CLI
perspicacite -c config.yml add-to-kb my-kb --from-bibtex more-refs.bib

# Via REST API (synchronous, small sets)
curl -X POST http://localhost:5468/api/kb/my-kb/bibtex \
  -H "Content-Type: application/json" \
  -d '{"bibtex": "<bib content as string>"}'

# Via REST API (asynchronous, recommended for > 20 papers)
curl -X POST http://localhost:5468/api/kb/my-kb/bibtex/async \
  -H "Content-Type: application/json" \
  -d '{"bibtex": "<bib content as string>"}'
# → {"job_id": "..."}
# Then poll: curl -sN http://localhost:5468/api/jobs/<job_id>/events
```

Duplicate DOIs are detected at the KB level and skipped — safe to re-run with
overlapping `.bib` files.

---

## Handling paywalled papers

Papers behind a paywall with no open-access version are stored as abstracts. To reach
them via institutional access, set up browser cookie export:

```bash
uv pip install -e ".[cookies]"
perspicacite import-browser-cookies \
  --browser brave \
  --domain nature.com \
  --domain sciencedirect.com \
  --output ~/.config/perspicacite/cookies.txt
```

Then in `config.yml`:

```yaml
pdf_download:
  cookies_path: "~/.config/perspicacite/cookies.txt"
  cookie_domains:
    - "nature.com"
    - "sciencedirect.com"
```

See [guides/institutional-pdf-access.md](institutional-pdf-access.md) for the full
walkthrough.

---

## Checking results

```bash
# Paper count, chunk count, year distribution, source breakdown
curl http://localhost:5468/api/kb/my-kb/stats

# List papers (JSON)
perspicacite list-kb --json
```

The `stats` response includes a `content_types` breakdown showing how many papers were
ingested at each level (`structured`, `full_text`, `abstract`, `none`).

---

## Related topics

- [concepts/knowledge-bases.md](../concepts/knowledge-bases.md) — KB storage
  internals
- [guides/search-to-kb.md](search-to-kb.md) — building a KB from a literature search
  without a pre-existing `.bib`
- [guides/institutional-pdf-access.md](institutional-pdf-access.md) — reaching
  paywalled PDFs
- [reference/cli.md](../reference/cli.md) — `create-kb` and `add-to-kb` flags
