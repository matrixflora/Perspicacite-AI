# Zotero Integration

Perspicacité integrates with Zotero in two directions: you can push papers from a KB
to your Zotero library (with PDF and supplementary file attachments), and you can build
KBs from your existing Zotero collections. This guide covers both.

---

## Prerequisites

- A Zotero account with API access: [zotero.org/settings/keys](https://www.zotero.org/settings/keys)
- Your Zotero library ID (visible at `https://www.zotero.org/settings/keys` or in the
  URL of your group library)
- `ZOTERO_API_KEY` environment variable set, or `zotero.api_key` in `config.yml`

---

## Configuration

```yaml
# config.yml
zotero:
  enabled: true
  api_key: ""             # or set ZOTERO_API_KEY env var
  library_id: "5691738"   # your user or group library ID
  library_type: "user"    # "user" or "group"
  base_url: "https://api.zotero.org"  # default; change for local desktop API
```

Environment variables override `config.yml`:

```bash
export ZOTERO_API_KEY="your-key"
export PERSPICACITE_ZOTERO_LIBRARY_ID="5691738"
export PERSPICACITE_ZOTERO_BASE_URL="http://localhost:23119/api"  # local desktop API
```

---

## Pushing papers to Zotero

### Via MCP

```python
# Push papers by DOI, attach cached PDFs and SI files
await push_to_zotero(
    dois=["10.1038/s41586-023-06924-6", "10.1002/jcc.21366"],
    attach_pdf=True,
    attach_supplementary=True,
)
# → {"created": [{"doi": "...", "key": "ABCD1234", "attached_pdf": true,
#                 "attached_supplementary": ["table1.xlsx"]}]}
```

### Via REST API

```bash
curl -X POST http://localhost:5468/api/zotero/push \
  -H "Content-Type: application/json" \
  -d '{"dois": ["10.1038/s41586-023-06924-6"], "attach_pdf": true}'
```

### Check Zotero status

```bash
curl http://localhost:5468/api/zotero/status
# → {"connected": true, "library_id": "5691738", "library_type": "user"}
```

### Notes on attachment upload

- Attachment upload uses Zotero's 4-step Web API file-upload protocol (register shell
  → request credentials → multipart POST to storage → finalize).
- Cloud API only (`api.zotero.org`) — the local desktop API is read-only.
- If no cached PDF exists for a DOI, Perspicacité attempts a live fetch before
  uploading. This populates the PDF cache as a side effect.
- Supplementary files are uploaded from `data/capsules/<paper_id>/supplementary/files/`
  — build capsules first with `build-capsules --kb <name>`.

---

## Building KBs from Zotero collections

The `build_kbs_from_zotero` MCP tool (or REST `POST /api/zotero-ingest/build-kbs/async`)
creates one KB per top-level Zotero collection in your library:

```python
await build_kbs_from_zotero(
    library_id="5691738",    # optional override; uses config default if omitted
)
```

Before committing, preview what KBs would be created:

```bash
curl "http://localhost:5468/api/zotero-ingest/plan"
# → {"collections": [{"name": "Diamond Sensors", "item_count": 24}, ...]}
```

Then trigger the build:

```bash
curl -X POST "http://localhost:5468/api/zotero-ingest/build-kbs/async" \
  -H "Content-Type: application/json" \
  -d '{"library_id": "5691738"}'
# → {"job_id": "..."}
# Poll: curl -sN http://localhost:5468/api/jobs/<job_id>/events
```

Each collection becomes one KB named after the collection (sanitized to
`perspicacite_<name>`). Papers in sub-collections are included in the parent
collection's KB. If a KB with the same name already exists, papers are appended.

---

## Using the local Zotero desktop API

The Zotero desktop app exposes a local HTTP API on port 23119 that you can use
instead of the cloud API to avoid rate limits and reach Linked Files / ZotFile-managed
PDFs that are not synced to the cloud.

Setup:
1. Zotero 7+: Settings → Advanced → check "Allow other applications on this computer
   to communicate with Zotero". Zotero 6: `about:config` →
   `extensions.zotero.httpServer.enabled = true`.
2. Restart Zotero.
3. Set `base_url: "http://localhost:23119/api"` in `config.yml` or via the
   `PERSPICACITE_ZOTERO_BASE_URL` env var.

Note: the local API is read-only for Perspicacité's purposes (KB building is possible;
pushing new items is not).

---

## Related topics

- [guides/obsidian-export.md](obsidian-export.md) — alternative export format
- [concepts/capsules.md](../concepts/capsules.md) — building capsules before SI attachment
- [reference/mcp-tools.md](../reference/mcp-tools.md) — `push_to_zotero` and
  `build_kbs_from_zotero` tool signatures
- [reference/rest-api.md](../reference/rest-api.md) — Zotero REST endpoints
