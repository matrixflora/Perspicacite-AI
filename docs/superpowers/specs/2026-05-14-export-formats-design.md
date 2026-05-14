# Export formats: CSL JSON + RIS — design spec

**Wave 4.5 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Extend the existing BibTeX exporter to also emit CSL JSON
and RIS. These two formats round out the reference-manager interop
story (Zotero, Mendeley, EndNote, Papers, Hayagriva, Pandoc all read
one or both).

## Existing surface (unchanged)

`src/perspicacite/pipeline/export_kb.py` already provides:

- `render_bibtex_entry(paper, file_path=None) -> str`
- `export_kb(app_state, kb_name, out_dir, ..., overwrite=False) -> ExportReport`

The BibTeX path writes `<kb_name>.bib`. Wave 4.5 keeps this exact
behaviour by default and adds two new format options.

## Architecture

Two new pure-function renderers:

```python
def render_csl_json_entry(paper: dict) -> dict: ...
def render_ris_entry(paper: dict) -> str: ...
```

Plus a `formats: list[Literal["bibtex", "csl_json", "ris"]]`
parameter on `export_kb`. Default `["bibtex"]` (today's behaviour
unchanged). When the list contains:

| Format | Output file |
|---|---|
| `bibtex` | `<kb>.bib` (existing) |
| `csl_json` | `<kb>.csl.json` (JSON array of CSL items) |
| `ris` | `<kb>.ris` (RIS records concatenated with blank lines) |

`ExportReport` grows new fields `csl_json_entries` and `ris_entries`.

## Format details

### CSL JSON

Canonical schema:
[https://github.com/citation-style-language/schema](https://github.com/citation-style-language/schema).

```json
[
  {
    "id": "smith2024title",
    "type": "article-journal",
    "title": "Paper title",
    "author": [{"family": "Smith", "given": "J."}],
    "issued": {"date-parts": [[2024]]},
    "container-title": "Journal Name",
    "DOI": "10.1234/example",
    "URL": "https://doi.org/10.1234/example",
    "abstract": "..."
  },
  ...
]
```

`id` reuses the BibTeX citation key for cross-format consistency.
Author parsing: split on `","` or `" and "`, treat the first comma-
separated chunk as `family`, the rest as `given`.

### RIS

```
TY  - JOUR
ID  - smith2024title
T1  - Paper title
AU  - Smith, J.
PY  - 2024
JO  - Journal Name
DO  - 10.1234/example
UR  - https://doi.org/10.1234/example
AB  - ...
ER  -
```

Tag list: `TY` (record type), `ID`, `T1` (title), `AU` (one per
author), `PY` (year), `JO` (journal), `DO` (DOI), `UR` (URL), `AB`
(abstract), `ER` (end of record). One field per line; multi-author
papers repeat `AU`.

## Components

| File | Change |
|---|---|
| `src/perspicacite/pipeline/export_kb.py` | Add `render_csl_json_entry`, `render_ris_entry`, `formats` param on `export_kb`, write extra output files. |
| `tests/unit/test_export_formats.py` (new) | Renderer correctness (fields, escaping, multi-author handling); `export_kb` writes the right files for each format. |
| `docs/export-formats-2026-05-14.md` (new) | Operator guide. |

## Behaviour contract

- `formats=["bibtex"]` (default) → today's exact behaviour.
- `formats=["bibtex", "csl_json"]` → both files written. Existing
  BibTeX overwrite semantics apply to all files in the format list
  (a CSL JSON or RIS file that already exists also requires
  `overwrite=True`).
- `formats=[]` → raise `ValueError`. (Empty list is operator error.)
- Unknown format string → raise `ValueError("unknown format: ...")`.
- Papers with missing DOI → still exported. The `id` field falls back
  to a derived key just like BibTeX.

## Test plan

- `test_csl_json_basic_fields`
- `test_csl_json_multi_author_split`
- `test_csl_json_handles_string_authors`
- `test_csl_json_omits_missing_fields` (no DOI, no abstract, etc.)
- `test_ris_basic_fields`
- `test_ris_multi_author_repeats_AU`
- `test_ris_escapes_field_values` (no newlines inside fields)
- `test_export_kb_default_formats_unchanged` (bibtex-only when not
  specified)
- `test_export_kb_all_three_formats` (writes .bib, .csl.json, .ris)
- `test_export_kb_unknown_format_raises`
- `test_export_kb_empty_formats_raises`

## Followups

- Hayagriva YAML (Typst's bibliography format).
- EndNote XML.
- Citation key consistency across formats (the BibTeX key is the
  current authority — make sure CSL `id` and RIS `ID` match).
- Round-trip: export → re-ingest test, especially for CSL JSON
  which Zotero re-imports.
