# Capsules

A capsule is an optional per-paper enrichment layer that extracts and indexes
structured sub-content from a paper: figures with captions, a parsed references list,
code snippets and script URLs, and supplementary information (SI) files. Capsules make
it possible to answer questions that require reasoning about a specific figure, a
specific referenced GitHub repository, or a specific supplementary table — not just the
main text.

---

## What a capsule contains

```
data/capsules/<paper_id>/
    metadata.json           # DOI, title, content_type, build timestamp
    figures/
        fig_1.png           # extracted figure image
        fig_1_caption.txt   # figure caption text
        fig_2.png
        fig_2_caption.txt
        ...
    references/
        references.json     # parsed reference list [{title, doi, year, authors}, ...]
    code/
        urls.json           # GitHub / Zenodo / Crossref resource URLs mined from the text
        snippets/           # fetched code files (when fetch_paper_resources was run)
    supplementary/
        files/              # SI files downloaded from PMC OA S3, Springer ESM, ACS
        manifest.json       # {filename, source, size, sha256} per SI file
```

Not all sections are present for every paper. A paper served as `content_type:
"abstract"` will have very sparse capsule content — typically just the reference list
parsed from CrossRef metadata. Structured-content papers (PMC JATS, arXiv HTML) tend
to produce the richest capsules.

---

## Building capsules

### For a single paper

```bash
perspicacite -c config.yml build-capsule --paper-id "10.1038/s41586-023-06924-6" --kb my-kb
```

The `--paper-id` argument accepts DOIs, PMIDs, or the internal UUID assigned at
ingest time.

### For all papers in a KB (idempotent)

```bash
perspicacite -c config.yml build-capsules --kb my-kb
```

Papers that already have a capsule are skipped unless `--force` is passed. Safe to
re-run after adding new papers.

### Via MCP

```python
# Build for a single paper
await build_capsule(paper_id="10.1038/s41586-023-06924-6", kb_name="my-kb")

# Build for all papers in a KB
await build_capsules_for_kb(kb_name="my-kb")
```

---

## Fetching external resources

The `fetch-resources` command mines external URLs from a paper's text (GitHub
repository links, Zenodo records, Crossref-linked datasets) and optionally downloads
the referenced files:

```bash
perspicacite -c config.yml fetch-resources \
  --paper-id "10.1038/s41586-023-06924-6" \
  --kb my-kb
```

Resource URLs are written to `data/capsules/<paper_id>/code/urls.json`. Text files
(`.py`, `.R`, `.md`, `.yml`, etc.) within size limits are fetched and stored under
`code/snippets/`. Binary files and files exceeding
`external_resources.zenodo_max_bytes_per_file` are skipped.

Supplementary information files can be fetched separately:

```bash
# Via MCP
await fetch_supplementary(paper_id="10.1038/s41586-023-06924-6", kb_name="my-kb")
```

SI sources tried in order: PMC OA S3 → Springer ESM → ACS.

---

## Capsule-aware retrieval

When capsules are built, the RAG engine can include figure captions and code snippets
in the embedding index alongside the main text chunks. This allows queries like:

- "What does Figure 3 show in the paper about X?"
- "Which GitHub repositories are referenced in papers about Y?"
- "What supplementary tables are available for paper Z?"

Capsule-aware retrieval is enabled by default when capsules exist. The `multimodal`
config section controls whether figure images are attached to answers:

```yaml
capsule:
  build_on_add: false         # auto-build capsule when adding a paper (off by default)

multimodal:
  mode: "auto"                # auto | force | off
  # "auto": attach figure images when retrieved chunks reference a figure
  # "force": also pull top-N figures by caption relevance
  # "off": never attach figure images
```

---

## Zotero attachment push with capsule SI

When pushing papers to Zotero, capsule supplementary files can be attached alongside
the cached PDF:

```python
await push_to_zotero(
    dois=["10.1038/s41586-023-06924-6"],
    attach_pdf=True,
    attach_supplementary=True,   # uploads data/capsules/<doi>/supplementary/files/*
)
```

This uses Zotero's 4-step Web API file-upload protocol and requires cloud API access
(`api.zotero.org`). See [guides/zotero-integration.md](../guides/zotero-integration.md).

---

## Related topics

- [guides/ingest-bibtex.md](../guides/ingest-bibtex.md) — ingesting papers that will
  have capsules built on them
- [concepts/provenance.md](provenance.md) — how capsule content appears in the
  retrieval trace
- [reference/cli.md](../reference/cli.md) — `build-capsule`, `build-capsules`,
  `fetch-resources` subcommand flags
- [reference/mcp-tools.md](../reference/mcp-tools.md) — `build_capsule`,
  `build_capsules_for_kb`, `fetch_paper_resources`, `fetch_supplementary` MCP tools
