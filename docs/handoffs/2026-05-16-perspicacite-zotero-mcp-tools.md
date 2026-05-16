# Perspicacité handoff — Zotero MCP tools for ASB integration

**Date:** 2026-05-16
**Drop into:** a fresh Claude session on the `Perspicacite-AI` repo
**Branch to work on:** `main` (per-task commits, never push — standing workflow)
**Paired work:** ASB maintainers are implementing the ASB-side caller in parallel

---

## 1. Why ASB needs these tools

AgenticScienceBuilder (ASB) generates structured skill bundles from scientific papers.
Each skill describes a focused scientific workflow: which tools to use, which papers back
the methodology, what parameters are typical, and what environments are required.

To generate a skill bundle from a Zotero collection, ASB needs to:

1. **Discover** which Zotero collections exist in Perspicacité's library (domain catalog).
2. **List** the papers in a collection with full metadata and license classification.
3. **Fetch** full-text PDFs and supplementary files for each paper (local cache first, remote fallback).
4. **Trigger** Perspicacité to build a KB from a collection so ASB can query it via RAG during skill generation.

Perspicacité already has Zotero integration (`push_to_zotero`, `build_kbs_from_zotero`)
but exposes no read-path tools for external agents. These 4 new MCP tools close that gap.

**Existing capability to preserve:** Perspicacité ingests ASB skill bundles via the
github-skill-bundle-ingest path (`docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md`
and `docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md`). That direction
(Perspicacité consuming ASB output) is independent and must not be disturbed.

---

## 2. License policy

When Perspicacité serves paper content to ASB, it classifies each paper's license and
attaches a `policy` field telling ASB what it may do with the text.

**Classification:**

| License type | Examples | `classification` | `policy` |
|---|---|---|---|
| Permissive open access | CC0, CC-BY-\*, CC-BY-SA-\*, MIT, Apache-2.0, BSD-\* | `permissive` | `verbatim` |
| Restrictive / closed | CC-BY-NC-\*, CC-BY-ND-\*, © All rights reserved, no license | `closed` | `reflavor` |
| Unknown | Anything else, lookup failure | `unknown` | `reflavor` (safe default) |

**Policy semantics (for ASB):**
- `verbatim` — ASB may copy text from this paper directly into skill documentation.
- `reflavor` — ASB must paraphrase/summarize rather than reproducing text verbatim.

**Resolution order (first hit wins):**
1. Crossref license metadata (`GET /works/{doi}` → `license[].URL`)
2. OpenAlex `open_access.license` field
3. Unpaywall `best_oa_location.license`
4. Zotero item tags (user-applied: `cc-by`, `open-access`, `closed`)
5. Heuristic: if OpenAlex `is_oa=true` with no explicit license → `permissive`, low confidence

License results are cached per DOI with a 7-day TTL.

---

## 3. Full JSON-RPC contract

All tools use the existing MCP streamable-HTTP transport.

**Session setup (same as all Perspicacité MCP tools):**
```python
import httpx

r = httpx.post("http://localhost:5468/mcp", json={
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "asb", "version": "1.0"}
    }
}, headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
session_id = r.headers["mcp-session-id"]
```

---

### Tool 1 — `zotero_list_collections`

List all top-level and sub-collections in the Zotero library.

**ASB calls this:** once at the start of a run to map domain names to collection IDs.

**Caching:** 1 hour, keyed on `(library_id, zotero_library_version)` using Zotero's
`Last-Modified-Version` response header. ASB should not hammer this; one call per session
is sufficient.

```json
// Request
{
  "jsonrpc": "2.0", "id": 2,
  "method": "tools/call",
  "params": {
    "name": "zotero_list_collections",
    "arguments": {
      "library_id": "5691738",          // optional — defaults to config zotero.library_id
      "include_subcollections": true    // default true
    }
  }
}

// Success response (result.content[0].text, parsed JSON)
{
  "collections": [
    {
      "id": "ABC123",
      "name": "Metabolomics",
      "parent_id": null,
      "item_count": 42,
      "subcollections": [
        {
          "id": "DEF456",
          "name": "MS1 methods",
          "parent_id": "ABC123",
          "item_count": 11,
          "subcollections": []
        }
      ]
    }
  ],
  "library_id": "5691738",
  "library_type": "user"
}
```

**Error codes:**

| Code | Name | When |
|---|---|---|
| -32001 | `ZOTERO_NOT_CONFIGURED` | No API key or library_id in config |
| -32002 | `ZOTERO_AUTH_FAILED` | 403 from Zotero API |
| -32003 | `ZOTERO_RATE_LIMITED` | 429 from Zotero API; response includes `retry_after_s` |
| -32004 | `LIBRARY_NOT_FOUND` | 404 for the given library_id |

---

### Tool 2 — `zotero_get_collection_items`

Return all papers in a collection with metadata and per-paper license classification.

**ASB calls this:** to build its working list of papers for a skill bundle. Use cursor
pagination for collections larger than 200 items.

**Caching:** item list invalidated when the Zotero collection version changes (server
tracks via `Last-Modified-Version`). Cursor tokens expire after 10 minutes.

```json
// Request
{
  "jsonrpc": "2.0", "id": 3,
  "method": "tools/call",
  "params": {
    "name": "zotero_get_collection_items",
    "arguments": {
      "collection_id": "ABC123",        // required
      "library_id": "5691738",          // optional
      "include_abstract": true,         // default true
      "limit": 200,                     // default 200, max 500
      "cursor": null                    // pagination token from previous call, or omit
    }
  }
}

// Success response
{
  "collection_id": "ABC123",
  "items": [
    {
      "zotero_key": "WXYZ9876",
      "doi": "10.1021/acs.jproteome.4c01051",
      "title": "metLinkR: Facilitating Metaanalysis of Human Metabolomics Data",
      "authors": ["Smith J", "Jones K"],
      "year": 2025,
      "abstract": "...",                // null when include_abstract=false
      "item_type": "journalArticle",
      "tags": ["metabolomics", "identifier-mapping"],
      "license": {
        "spdx": "CC-BY-4.0",
        "classification": "permissive",
        "policy": "verbatim",
        "source": "crossref"            // "crossref"|"openalex"|"unpaywall"|"zotero_tag"|"heuristic"|"unknown"
      },
      "has_attachments": true           // call zotero_get_paper_resources to get file access
    }
  ],
  "total": 42,
  "next_cursor": null                   // null means no more pages
}
```

**Pagination pattern for ASB:**
```python
cursor = None
all_items = []
while True:
    resp = call_tool("zotero_get_collection_items",
                     {"collection_id": "ABC123", "cursor": cursor})
    all_items.extend(resp["items"])
    cursor = resp["next_cursor"]
    if cursor is None:
        break
```

**Error codes:**

| Code | Name | When |
|---|---|---|
| -32001 | `ZOTERO_NOT_CONFIGURED` | No API key or library_id in config |
| -32002 | `ZOTERO_AUTH_FAILED` | 403 from Zotero API |
| -32003 | `ZOTERO_RATE_LIMITED` | 429; includes `retry_after_s` |
| -32005 | `COLLECTION_NOT_FOUND` | 404 for the given collection_id |
| -32006 | `INVALID_CURSOR` | Stale or malformed pagination token |

---

### Tool 3 — `zotero_get_paper_resources`

For a single paper, return an ordered list of file access options. Local paths are
returned first (Perspicacité's PDF cache and capsule storage); remote URLs follow as
fallbacks in priority order.

**ASB calls this:** for each paper it wants to process deeply. ASB tries access options
in order until one succeeds (local path = open the file; remote = download).

**Caching:** local paths verified at call time (os.path.exists, no TTL). License
classification re-used from the 7-day DOI cache populated by Tool 2.

```json
// Request
{
  "jsonrpc": "2.0", "id": 4,
  "method": "tools/call",
  "params": {
    "name": "zotero_get_paper_resources",
    "arguments": {
      "doi": "10.1021/acs.jproteome.4c01051",   // doi OR zotero_key required (not both)
      "zotero_key": null,
      "library_id": "5691738"                    // optional
    }
  }
}

// Success response
{
  "doi": "10.1021/acs.jproteome.4c01051",
  "zotero_key": "WXYZ9876",
  "license": {
    "spdx": "CC-BY-4.0",
    "classification": "permissive",
    "policy": "verbatim",
    "source": "crossref"
  },
  "resources": [
    {
      "role": "fulltext_pdf",
      "filename": "paper.pdf",
      "access": [
        {
          "type": "local",
          "path": "/Users/user/perspicacite/data/cache/pdfs/10.1021_acs.jproteome.4c01051.pdf"
        },
        {
          "type": "remote",
          "url": "https://unpaywall.org/10.1021/acs.jproteome.4c01051",
          "via": "unpaywall"
        },
        {
          "type": "remote",
          "url": "https://doi.org/10.1021/acs.jproteome.4c01051",
          "via": "doi_resolver"
        }
      ]
    },
    {
      "role": "supplementary",
      "filename": "table_S1.xlsx",
      "access": [
        {
          "type": "local",
          "path": "/Users/user/perspicacite/data/capsules/<paper_id>/supplementary/files/table_S1.xlsx"
        },
        {
          "type": "remote",
          "url": "https://pubs.acs.org/doi/suppl/10.1021/acs.jproteome.4c01051/...",
          "via": "publisher"
        }
      ]
    }
  ],
  "notes": []                           // Zotero notes items (plain text, if any)
}
```

**Remote `via` values and priority order:**
1. `"unpaywall"` — open-access PDF, most reliable for OA papers
2. `"publisher"` — Zotero attachment URL (requires institutional access for closed papers)
3. `"doi_resolver"` — `https://doi.org/{doi}` last resort

**Error codes:**

| Code | Name | When |
|---|---|---|
| -32001 | `ZOTERO_NOT_CONFIGURED` | No API key or library_id in config |
| -32002 | `ZOTERO_AUTH_FAILED` | 403 from Zotero API |
| -32003 | `ZOTERO_RATE_LIMITED` | 429; includes `retry_after_s` |
| -32007 | `PAPER_NOT_FOUND` | DOI or key not in Zotero library |
| -32008 | `AMBIGUOUS_DOI` | DOI matches >1 Zotero item; pass `zotero_key` instead |

---

### Tool 4 — `zotero_ingest_collection_to_kb`

Trigger Perspicacité to ingest a Zotero collection into a named KB. Returns immediately
with a `job_id`; ASB polls the job URL for completion before issuing RAG queries.

**ASB calls this:** when it wants to use Perspicacité's RAG (`search_knowledge_base`,
`generate_report`) during skill generation instead of processing raw PDFs itself. The
resulting KB name is what ASB passes to RAG tools.

**Caching / idempotency:** re-ingesting an already-indexed DOI is a no-op by default
(same dedup guard as `ingest_dois_into_kb`). Set `force_reingest=true` to re-embed.

```json
// Request
{
  "jsonrpc": "2.0", "id": 5,
  "method": "tools/call",
  "params": {
    "name": "zotero_ingest_collection_to_kb",
    "arguments": {
      "collection_id": "ABC123",          // required
      "kb_name": "metabolomics_ms1",      // optional — defaults to sanitized collection name
      "library_id": "5691738",            // optional
      "force_reingest": false             // default false
    }
  }
}

// Success response (job started)
{
  "job_id": "job_20260516_abc123_x9k2",
  "kb_name": "metabolomics_ms1",
  "collection_id": "ABC123",
  "item_count": 42,
  "status": "running",
  "poll_url": "http://localhost:5468/api/jobs/job_20260516_abc123_x9k2/events"
}
```

**ASB polling pattern:**
```python
import httpx, json

# Trigger ingest
resp = call_tool("zotero_ingest_collection_to_kb",
                 {"collection_id": "ABC123", "kb_name": "metabolomics_ms1"})
poll_url = resp["poll_url"]
kb_name = resp["kb_name"]

# Poll for completion (SSE stream)
with httpx.stream("GET", poll_url) as stream:
    for line in stream.iter_lines():
        if line.startswith("data:"):
            event = json.loads(line[5:])
            if event.get("status") in ("completed", "failed"):
                break

# Now use the KB
results = call_tool("search_knowledge_base",
                    {"kb_name": kb_name, "query": "identifier mapping threshold"})
```

**Error codes:**

| Code | Name | When |
|---|---|---|
| -32001 | `ZOTERO_NOT_CONFIGURED` | No API key or library_id in config |
| -32002 | `ZOTERO_AUTH_FAILED` | 403 from Zotero API |
| -32003 | `ZOTERO_RATE_LIMITED` | 429; includes `retry_after_s` |
| -32005 | `COLLECTION_NOT_FOUND` | 404 for the given collection_id |
| -32009 | `KB_NAME_CONFLICT` | `kb_name` exists with a different source collection; rename or set `force_reingest=true` |

---

## 4. How ASB calls these tools — end-to-end example

```python
# Step 1: discover available domains
collections = call_tool("zotero_list_collections", {})
metabolomics = next(c for c in collections["collections"] if "Metabolomics" in c["name"])

# Step 2: get all papers in the collection
items = []
cursor = None
while True:
    page = call_tool("zotero_get_collection_items",
                     {"collection_id": metabolomics["id"], "cursor": cursor})
    items.extend(page["items"])
    cursor = page["next_cursor"]
    if not cursor:
        break

# Step 3: for each paper, get file access (process permissive ones first)
for item in items:
    if item["license"]["policy"] == "verbatim":
        resources = call_tool("zotero_get_paper_resources", {"doi": item["doi"]})
        for r in resources["resources"]:
            if r["role"] == "fulltext_pdf":
                # try local path first
                for access in r["access"]:
                    if access["type"] == "local":
                        pdf_bytes = open(access["path"], "rb").read()
                        break
                    elif access["type"] == "remote":
                        pdf_bytes = httpx.get(access["url"]).content
                        break

# Step 4: optionally build a Perspicacité KB and use RAG
ingest = call_tool("zotero_ingest_collection_to_kb",
                   {"collection_id": metabolomics["id"], "kb_name": "metabolomics_asb"})
# ... poll ingest["poll_url"] until done ...
rag_result = call_tool("search_knowledge_base",
                       {"kb_name": "metabolomics_asb", "query": "MS1 peak picking threshold"})
```

---

## 5. Suggested 9-task implementation outline (for `/writing-plans`)

Drop this outline into the writing-plans skill to generate the full task-by-task plan.

**Task 1 — License classifier**
New file: `src/perspicacite/integrations/zotero/license.py`
`LicenseClassifier` with resolution chain: Crossref → OpenAlex → Unpaywall → Zotero tags → heuristic.
Returns `LicenseInfo(spdx: str | None, classification: str, policy: str, source: str)`.
7-day TTL cache via the existing `LLMCache` / disk-cache pattern.

**Task 2 — Resource locator**
New file: `src/perspicacite/integrations/zotero/resources.py`
`ResourceLocator.get_resources(doi, zotero_item)` → `list[Resource]`.
Each `Resource` has `role`, `filename`, `access: list[AccessOption]`.
Local path existence checked at call time (`os.path.exists`). Remote options built from
Unpaywall response + Zotero attachment URL + DOI resolver.

**Task 3 — `zotero_list_collections` MCP tool**
Add to `src/perspicacite/mcp/server.py`.
Call existing Zotero client `GET /users/{library_id}/collections` (recurse for sub-collections).
Apply 1-hour response cache keyed on `(library_id, Last-Modified-Version)`.
Raise typed errors for the 4 error codes.

**Task 4 — `zotero_get_collection_items` MCP tool**
Add to `src/perspicacite/mcp/server.py`.
Paginate via Zotero's `start`/`limit` params; map to opaque cursor tokens (base64 of `start` int).
Batch license enrichment: collect all DOIs per page → one Crossref batch call (`/works?filter=doi:{doi1}|{doi2}|...`) → map results back.
Assemble response per the contract above.

**Task 5 — `zotero_get_paper_resources` MCP tool**
Add to `src/perspicacite/mcp/server.py`.
Accept `doi` OR `zotero_key`; resolve the other via Zotero API if needed.
Detect `AMBIGUOUS_DOI` (Zotero `GET /items?q={doi}` returns >1 result).
Call `ResourceLocator`; return ordered `resources[]`.

**Task 6 — `zotero_ingest_collection_to_kb` MCP tool**
Add to `src/perspicacite/mcp/server.py`.
Fetch DOI list from Zotero collection → filter to items with DOIs → hand to existing
`ingest_dois_into_kb` job infrastructure.
Return `job_id` + `poll_url` (existing pattern from `build_kbs_from_zotero`).
`KB_NAME_CONFLICT` check: if a KB with that name exists and its source collection_id differs, raise -32009.

**Task 7 — Error handling wire-up**
Define `ZoteroMCPError(code, message, data)` in `src/perspicacite/integrations/zotero/errors.py`.
Map all 9 error codes to typed exceptions; ensure MCP server translates them to JSON-RPC
error objects. Confirm `ZOTERO_RATE_LIMITED` always includes `retry_after_s` in `data`.

**Task 8 — Unit tests**
`tests/unit/test_zotero_license.py` — 12+ cases: known permissive SPDX, known closed SPDX,
missing license fallback chain, CC-BY-NC should be closed, heuristic path.
`tests/unit/test_zotero_resources.py` — local-first ordering with mocked `os.path.exists`,
remote-only when no local cache, access list empty when all remotes unavailable.
`tests/unit/test_zotero_mcp_tools.py` — all 9 error codes, valid request/response shapes
(mock Zotero API via `httpx` pytest fixtures), cursor pagination round-trip.

**Task 9 — Documentation**
Update `docs/reference/mcp-tools.md` with all 4 new tools (parameters, return shapes,
error codes). Add an "ASB integration" section to `docs/guides/zotero-integration.md`
with the end-to-end example from Section 4 of this handoff.

---

## 6. Acceptance criteria

- [ ] All 4 tools respond correctly to valid requests against a live Zotero account
  (`ZOTERO_API_KEY` + `PERSPICACITE_ZOTERO_LIBRARY_ID` set).
- [ ] License policy field present on every item returned by Tools 2 and 3.
- [ ] Tool 3 (`zotero_get_paper_resources`) returns local paths only for files that exist
  on disk; never returns a stale local path.
- [ ] Tool 4 poll URL streams events and terminates with `status: "completed"` or
  `status: "failed"` — not a silent timeout.
- [ ] All 9 error codes are exercised in unit tests with mocked Zotero 4xx responses.
- [ ] `ZOTERO_RATE_LIMITED` error always includes `retry_after_s` in the error data dict.
- [ ] Existing `push_to_zotero` and `build_kbs_from_zotero` tools pass their existing
  tests unmodified (no signature regressions).
- [ ] `docs/reference/mcp-tools.md` updated to include all 4 new tools.

---

## 7. Cross-references

| Document | What it covers |
|---|---|
| `docs/superpowers/specs/2026-05-16-zotero-mcp-tools-design.md` | Full internal design spec (architecture, file list, test plan) |
| `docs/superpowers/specs/2026-05-15-asb-bundle-ingest-design.md` | The other direction: Perspicacité ingesting ASB skill bundles |
| `docs/superpowers/specs/2026-05-15-github-skill-bundle-ingest-design.md` | Generic GitHub repo / skill-bundle ingest (parent spec) |
| `docs/reference/mcp-tools.md` | Current MCP surface (23 tools pre-this work) |
| `docs/guides/zotero-integration.md` | Operator guide for Zotero config and existing tools |

---

## 8. Standing workflow (carry forward)

- Per-task commits directly to `main`; never push.
- Non-trivial work: brainstorm → spec → plan → subagent-driven execution.
- `PYTHONPATH=src` when running pytest inside a worktree.
- Heredoc commit messages with `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`.
- Memory at `~/.claude/projects/-Users-holobiomicslab-git-Perspicacite-AI/memory/`.
