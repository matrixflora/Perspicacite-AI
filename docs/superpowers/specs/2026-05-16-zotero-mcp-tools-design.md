# Zotero MCP Tools for ASB Integration — Design Spec

**Status:** Design accepted 2026-05-16. Not yet implemented.
**Companion handoff:** `docs/handoffs/2026-05-16-perspicacite-zotero-mcp-tools.md`

## Context

AgenticScienceBuilder (ASB) generates skill bundles from scientific papers. To do this it
needs to pull paper metadata, full-text PDFs, and supplementary files from Perspicacité's
Zotero library. This spec defines **4 new MCP tools** that expose the Zotero read path
to external agents (primarily ASB).

Existing tools (`push_to_zotero`, `build_kbs_from_zotero`) remain unchanged.
The Perspicacité→ASB ingest direction (github-skill-bundle-ingest spec) is a separate,
independent concern and is not modified here.

## Division of responsibility

- **ASB** discovers collections, fetches paper content via these 4 tools, generates skills.
- **Perspicacité** resolves license info, locates files (local cache first, remote fallback),
  and exposes Zotero collection data through a stable MCP contract.

## The 4 tools

### 1. `zotero_list_collections`

Enumerate the Zotero library tree so ASB knows which domains/topics are available.

**Request arguments:** `library_id` (optional, default from config), `include_subcollections` (bool, default true).

**Response:** flat list of `{id, name, parent_id, item_count, subcollections[]}` nodes.

**Cache:** 1 hour, keyed on `(library_id, zotero_library_version)` using Zotero's `Last-Modified-Version` header.

### 2. `zotero_get_collection_items`

Return all papers in a collection with full metadata and per-paper license classification.

**Request arguments:** `collection_id` (required), `library_id` (optional), `include_abstract` (bool, default true), `limit` (int, default 200, max 500), `cursor` (pagination token, optional).

**Response:** `{items[], total, next_cursor}` where each item carries: `zotero_key`, `doi`, `title`, `authors[]`, `year`, `abstract`, `item_type`, `tags[]`, `has_attachments`, and a `license` block: `{spdx, classification, policy, source}`.

**Cache:** cursor tokens valid 10 min; item list invalidated when Zotero collection version changes.

### 3. `zotero_get_paper_resources`

For a single paper, return an ordered list of file access options (local path first, then remote URLs).

**Request arguments:** `doi` OR `zotero_key` (one required), `library_id` (optional).

**Response:** `{doi, zotero_key, license, resources[]}` where each resource has `role` (`fulltext_pdf` | `supplementary` | `note`), `filename`, and an ordered `access[]` list: `[{type: "local", path: "..."}, {type: "remote", url: "...", via: "unpaywall"|"publisher"|"doi_resolver"}]`.

**Cache:** local paths verified at call time (no TTL); license classification cached 7 days per DOI.

### 4. `zotero_ingest_collection_to_kb`

Trigger ingestion of a Zotero collection into a Perspicacité KB (async job). Wraps
existing `build_kbs_from_zotero` logic with a collection-ID filter and optional KB name override.

**Request arguments:** `collection_id` (required), `kb_name` (optional), `library_id` (optional), `force_reingest` (bool, default false).

**Response:** `{job_id, kb_name, collection_id, item_count, status: "running", poll_url}`.

**Cache:** none (stateful job); idempotent by DOI unless `force_reingest=true`.

## License classifier

**Resolution order (first hit wins):**
1. Crossref license metadata (via `GET /works/{doi}` — `license[].URL`)
2. OpenAlex `open_access.license` field
3. Unpaywall `best_oa_location.license`
4. Zotero item tags (user-applied: `cc-by`, `open-access`, `closed`)
5. Heuristic: if OpenAlex `is_oa=true` with no explicit license → permissive, low confidence

**Classification mapping:**

| SPDX / pattern | classification | policy |
|---|---|---|
| CC0-1.0, CC-BY-\*, CC-BY-SA-\* | permissive | verbatim |
| MIT, Apache-2.0, BSD-\* | permissive | verbatim |
| CC-BY-NC-\*, CC-BY-ND-\* | closed | reflavor |
| No license / © All rights reserved | closed | reflavor |
| Anything else | unknown | reflavor (safe default) |

License classification results cached per DOI with 7-day TTL.

## Resource locator — access ordering

For each file attached to a Zotero item:
1. **Local cache** — check `data/cache/pdfs/<doi_slug>.pdf` and `data/capsules/<paper_id>/supplementary/files/`; include only if the path exists at call time.
2. **Unpaywall** — query `api.unpaywall.org/v2/{doi}?email=<config>` for `best_oa_location.url_for_pdf`.
3. **Publisher** — use Zotero attachment URL if present.
4. **DOI resolver** — `https://doi.org/{doi}` as last resort.

Multiple access options are returned in this priority order. ASB tries each in sequence until one succeeds.

## Error codes

| Code | Name | Condition |
|---|---|---|
| -32001 | ZOTERO_NOT_CONFIGURED | No API key or library_id in config |
| -32002 | ZOTERO_AUTH_FAILED | 403 from Zotero API |
| -32003 | ZOTERO_RATE_LIMITED | 429; `retry_after_s` in error data |
| -32004 | LIBRARY_NOT_FOUND | 404 for the given library_id |
| -32005 | COLLECTION_NOT_FOUND | 404 for the given collection_id |
| -32006 | INVALID_CURSOR | Stale or malformed pagination token |
| -32007 | PAPER_NOT_FOUND | DOI/key not in Zotero library |
| -32008 | AMBIGUOUS_DOI | DOI matches >1 Zotero item; use zotero_key |
| -32009 | KB_NAME_CONFLICT | KB exists with a different source; use force_reingest or rename |

## Implementation files

| File | Role |
|---|---|
| `src/perspicacite/integrations/zotero/license.py` (new) | LicenseClassifier — Crossref→OpenAlex→Unpaywall→tags→heuristic |
| `src/perspicacite/integrations/zotero/resources.py` (new) | ResourceLocator — builds ordered access list per paper |
| `src/perspicacite/mcp/server.py` | Add the 4 new tools |
| `tests/unit/test_zotero_license.py` (new) | License classifier unit tests |
| `tests/unit/test_zotero_resources.py` (new) | Resource locator unit tests |
| `tests/unit/test_zotero_mcp_tools.py` (new) | MCP tool validation + error-code tests |
| `tests/integration/test_zotero_mcp_e2e.py` (new) | End-to-end fixture-based integration test |
| `docs/reference/mcp-tools.md` | Add 4 new tools |
| `docs/guides/zotero-integration.md` | Add ASB usage section |

## Out of scope

- Writing to Zotero from ASB (deferred — not needed in v1)
- Bidirectional Zotero↔ASB sync
- Zotero group library write access
- WebDAV attachment storage (Perspicacité uses Zotero cloud storage only)

## Testing

- **Unit:** license classifier (10+ known SPDX → policy assertions, all 3 conflict-resolution
  cases), resource locator (local-first with mocked `os.path.exists`, remote fallback order),
  each tool's request validation and all 9 error codes.
- **Integration:** fixture Zotero API responses in `tests/fixtures/zotero/`; run
  `zotero_get_collection_items` end-to-end and assert `license.policy` is set and
  `access[]` ordering is local-before-remote.
- **Regression:** ensure existing `push_to_zotero` and `build_kbs_from_zotero` tools are
  unaffected (no signature changes).
