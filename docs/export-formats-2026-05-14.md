# Export formats — operator guide (2026-05-14)

Wave 4.5 of the framework-hardening roadmap. Export a KB's metadata
as BibTeX (existing), CSL JSON, or RIS for reference managers.

## Quick usage

```python
from perspicacite.pipeline.export_kb import export_kb

# Today (unchanged): BibTeX only.
await export_kb(
    app_state=state, kb_name="exposomics", out_dir="/tmp/export",
)

# All three formats:
await export_kb(
    app_state=state, kb_name="exposomics", out_dir="/tmp/export",
    formats=["bibtex", "csl_json", "ris"],
)
```

Output files:

| Format | Filename |
|---|---|
| `bibtex` | `<kb>.bib` |
| `csl_json` | `<kb>.csl.json` |
| `ris` | `<kb>.ris` |

## Format choices

- **BibTeX** (default): LaTeX, Zotero, JabRef. Native PDF attachment
  via `file = {…}`. Most universal.
- **CSL JSON**: Pandoc citeproc, Zotero (via better-bibtex export),
  any modern tooling that speaks CSL. Lossless author / date
  structure.
- **RIS**: EndNote, Mendeley, Papers, ProQuest, Web of Science.
  Older but widely supported.

## Behaviour contract

- `formats=None` (default) → BibTeX only, today's exact behaviour.
- `formats=[]` → `ValueError`. Empty list is operator error.
- `formats=["unknown"]` → `ValueError("unknown format: 'unknown'; ...")`.
- Each format's file overwrites only when `overwrite=True`.
- Citation key / `id` field is consistent across all three formats —
  derived from the BibTeX key (`<first-author><year><first-word>`).

## Limitations

- All records are typed as `article-journal` / `JOUR`. Books and
  chapters are not differentiated. Adding type discrimination
  requires a structured `type` field in the paper metadata, a
  separate followup.
- Authors are normalised to `family` / `given` pairs. Unicode/Asian
  name formats with no comma may end up with `family` only.
- RIS line wrapping is not enforced; newlines inside fields are
  collapsed to spaces so the file stays valid.

## Files

| File | Purpose |
|---|---|
| `src/perspicacite/pipeline/export_kb.py` | `render_bibtex_entry`, `render_csl_json_entry`, `render_ris_entry`, `export_kb` |
| `tests/unit/test_export_formats.py` | Per-format renderer tests |
| `tests/unit/test_export_kb_formats.py` | `export_kb` format-selection tests |

## Followups

- Hayagriva YAML (Typst's bibliography format).
- EndNote XML.
- Book / chapter / inproceedings types.
- Round-trip test: export → re-ingest verifies no metadata loss.
