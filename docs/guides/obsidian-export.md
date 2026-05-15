# Obsidian Vault Export

Perspicacité can export any knowledge base as an Obsidian-compatible Markdown vault.
Each paper becomes a Markdown note with YAML frontmatter, and the vault is delivered
as a zip archive you can drop into Obsidian's vault folder.

---

## Export via REST API

```bash
curl -o my-kb-vault.zip \
  "http://localhost:5468/api/kb/my-kb/export?format=obsidian-vault"
```

Unzip and open the folder in Obsidian (File → Open Vault → select the unzipped folder).

---

## Export via CLI

```bash
perspicacite -c config.yml export-kb --kb my-kb --format obsidian-vault --output ./exports/
```

This writes `exports/my-kb/` with one Markdown file per paper.

---

## Vault structure

```
my-kb/
    Paper Title One.md
    Paper Title Two.md
    ...
```

Each note has YAML frontmatter:

```yaml
---
title: "Full Paper Title"
authors:
  - "Author One"
  - "Author Two"
year: 2024
doi: "10.1038/s41586-023-06924-6"
journal: "Nature"
source: "openalex"
content_type: "structured"
citation_count: 47
tags:
  - perspicacite
  - my-kb
---
```

The note body contains the abstract and, if full text was retrieved, a summary of the
main sections. Figure captions from capsules are included when available.

---

## Using the vault in Obsidian

Once imported, you can:

- Use Obsidian's graph view to see co-citation relationships (papers sharing common
  references will cluster)
- Search across all notes with Obsidian's full-text search
- Add your own notes, tags, and links on top of the generated content
- Use Dataview (a popular Obsidian plugin) to query the YAML frontmatter — for
  example, list all papers published after 2022 by citation count

The exported vault is a static snapshot — it does not update automatically when you
add more papers to the KB. Re-export after significant additions.

---

## BibTeX + PDF export (for Zotero import)

For a citation-manager-friendly export with PDF files attached, use the BibTeX export
instead:

```bash
perspicacite -c config.yml export-kb \
  --kb my-kb \
  --output ~/exports/diamond \
  --with-pdfs

# Include supplementary information files from capsules:
perspicacite -c config.yml export-kb \
  --kb my-kb \
  --output ~/exports/diamond \
  --with-pdfs \
  --with-supplementary
```

Output:

```
~/exports/diamond/
    my-kb.bib                # one @article per paper, file = {…} BetterBibTeX field
    manifest.json            # export summary (counts, missing DOIs)
    papers/<doi>.pdf         # cached PDF copies
    supplementary/<id>/      # SI files from capsules (when --with-supplementary)
```

Drag `my-kb.bib` into Zotero (File → Import) — Zotero reads the `file` field and
attaches PDFs automatically.

---

## Via MCP

```python
await export_kb(
    kb_name="my-kb",
    format="obsidian-vault",   # or "bibtex"
)
# Returns a download URL or base64-encoded zip depending on transport
```

---

## Related topics

- [guides/zotero-integration.md](zotero-integration.md) — push directly to Zotero
  with PDF attachment, no intermediate zip step
- [reference/cli.md](../reference/cli.md) — `export-kb` flags
- [reference/rest-api.md](../reference/rest-api.md) — export endpoint
