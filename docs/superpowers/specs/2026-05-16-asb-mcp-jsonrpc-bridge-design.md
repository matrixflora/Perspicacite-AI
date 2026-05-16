# ASB ↔ Perspicacité MCP JSON-RPC Bridge — Design Spec

**Date:** 2026-05-16
**Status:** Draft — Perspicacité-side prerequisites landed in this session; ASB-side bridge work is the remaining piece.
**Owner:** Audit follow-up (Finding F1 from `2026-05-16-asb-perspicacite-live-audit-findings.md`)

## Goal

Bridge ASB's `MCPPerspicaciteClient` (in `agentic-science-builder/src/agentic_science_builder/perspicacite_client.py`) to Perspicacité's FastMCP `/mcp` streamable-http JSON-RPC endpoint, calling the existing `zotero_list_collections` / `zotero_get_collection_items` / `zotero_get_paper_resources` tools plus the new `zotero_get_attachment_bytes` tool added in this session. **Keep the 4-method `PerspicaciteClient` Protocol shape unchanged** so ASB's callers (`cli.py`, `package_synthesizer.synthesize_zotero_item`, `tests/test_perspicacite_zotero.py`) need no changes.

## Why this is needed

The 2026-05-16 audit confirmed live that ASB's current `MCPPerspicaciteClient`:

- POSTs to `<base_url>/` with `{"tool": "...", "args": {...}}` JSON
- Perspicacité actually serves FastMCP streamable-http JSON-RPC at `/mcp` with SSE
- The 4 tool names ASB calls (`list_zotero_libraries`, `list_zotero_collection`, `get_zotero_item_metadata`, `get_zotero_attachment`) **don't exist** on Perspicacité

Result: every `--source-kind=zotero-mcp` build against a real Perspicacité server fails (`POST /` → 404; `POST /mcp` → 406 "Not Acceptable").

## Perspicacité-side prerequisites — LANDED 2026-05-16

| Prereq | Where |
|--------|-------|
| `zotero_get_collection_items` returns `attachment_keys: list[str]` (was hardcoded `has_attachments: False`) | `src/perspicacite/mcp/server.py:2733-2756` |
| New `zotero_get_attachment_bytes(attachment_key, library_id?)` MCP tool returning `{filename, content_b64, content_type, size_bytes, role_hint?, license_spdx?}` | `src/perspicacite/mcp/server.py` Tool 16b |
| Tests covering both | `tests/unit/test_zotero_mcp_new_tools.py:test_get_collection_items_populates_attachment_keys`, `test_get_attachment_bytes_*` (4 tests) |

ASB-side work below depends only on these contracts, which are now stable in Perspicacité `main`.

## Architecture (ASB side)

### New `_MCPSession` helper class

Stdlib `urllib` only — no new ASB dependency. Encapsulates the FastMCP wire dance.

```python
class _MCPSession:
    def __init__(self, base_url: str, timeout_s: float = 10.0): ...
    # _endpoint = f"{base_url.rstrip('/')}/mcp"  (appended internally)

    def initialize(self) -> None:
        """POST JSON-RPC initialize with Accept: application/json,
        text/event-stream. Capture mcp-session-id response header.
        POST notifications/initialized. Idempotent."""

    def call_tool(self, name: str, arguments: dict) -> dict:
        """POST JSON-RPC tools/call with mcp-session-id. Parse the
        SSE-encoded JSON-RPC response (one `event: message\ndata: {...}`
        line per response). Return result.content[0].text JSON-decoded
        — FastMCP wraps each tool return in this content envelope."""

    def list_tools(self) -> list[str]:
        """For is_available() — fast probe, no full init dance."""
```

Constructor signature: `base_url: str` is the **server root** (e.g. `http://127.0.0.1:8765`), NOT the `/mcp` endpoint. The class appends `/mcp` internally. Backwards-compatible with the existing default since ASB's old default base_url was also `http://127.0.0.1:8765`.

### Updated `MCPPerspicaciteClient`

Same 4-method `PerspicaciteClient` Protocol. New internals:

- `_session: _MCPSession | None` — lazy-initialized in `is_available()` / first call.
- `_item_cache: dict[str, dict]` — per-instance cache. `list_zotero_collection` writes into it; `get_zotero_item_metadata` reads from it so we don't issue a wire call per item for fields Perspicacité only returns at the collection level.

#### Method mapping

| ASB Protocol method | Wire call(s) | Notes |
|---------------------|--------------|-------|
| `is_available()` | `tools/list` (short timeout) | True iff HTTP 200 + valid JSON-RPC response |
| `list_zotero_libraries()` | `zotero_list_collections({})` once | Synthesize a 1-element list: `[ZoteroLibrary(library_id=server.library_id, name=server.library_id, type=server.library_type)]`. Perspicacité is single-library-per-config — document the limitation. |
| `list_zotero_collection(col_key, library_id=None)` | `zotero_get_collection_items(collection_id=col_key, library_id=…)`, paginated via `next_cursor` | Mapping: `zotero_key → item_key`. Pull `attachment_keys` from the new field. Cache the full item dict at `_item_cache[item_key]` for downstream `get_zotero_item_metadata` calls. |
| `get_zotero_item_metadata(item_key)` | **No wire call** — read from `_item_cache` | Build `BiblioRecord` from cached fields (title, doi, authors, year, abstract, item_type, tags, license.spdx → `license_spdx`). Raise `RuntimeError("call list_zotero_collection first to populate the cache")` when the key isn't cached. |
| `get_zotero_attachment(att_key)` | `zotero_get_attachment_bytes(attachment_key=att_key)` | Base64-decode `content_b64` into `ZoteroAttachment.content`. |

#### Wire-name and arg-name reminders

ASB has historically used `item_key` and `collection_key`; Perspicacité uses `zotero_key` for items and `collection_id` for collection paths. **The mapping layer is the bridge's job.** Inside the bridge:

- input `collection_key` → wire arg `collection_id`
- input `library_id` → wire arg `library_id` (passes through)
- input `item_key` → reads `it["zotero_key"]` on the wire response
- input `attachment_key` → wire arg `attachment_key`

#### Pagination

`zotero_get_collection_items` returns `{"items": [...], "next_cursor": "..."|null}`. The bridge accumulates pages until `next_cursor is None`. Default `limit=50`; the bridge can raise to Perspicacité's max (500) for fewer round-trips on large collections, but stays at 50 by default to avoid request timeouts.

#### Search / passages methods — out of scope

`MCPPerspicaciteClient` also implements `search_related_papers` and `get_relevant_passages` used by the K3 enrichment loop. Confirmed via `tools/list`: **neither has an analog on Perspicacité.** The bridge should re-route those through `_MCPSession.call_tool` for transport consistency, but the wire calls will surface as `tools/call` errors (`"Unknown tool"`) until one side adds them. Document the gap; out of scope for this bridge.

## Tests

Replace `test_http_client_list_zotero_collection_calls_correct_method`:
- assert wire tool name is `zotero_get_collection_items`
- assert wire arg name is `collection_id` (not `collection_key`)
- assert pagination via `next_cursor` works (mock 2-page response, expect 2 wire calls, single concatenated list)

Add `test_mcp_session_initialize_and_tools_call`:
- monkeypatch `urllib.request.urlopen` with a fake that records calls
- assert: `/mcp` endpoint, `Accept: application/json, text/event-stream` header on first call, `mcp-session-id` header carried on second call, JSON-RPC envelope shape (`jsonrpc/id/method/params`)
- assert: base64 round-trip for `get_zotero_attachment` — pre-encode `b"%PDF-1.4 test"` as base64, return as `content_b64` in fake response, verify the decoded `ZoteroAttachment.content` matches

Add `test_mcp_client_caches_items_from_list_for_metadata`:
- call `list_zotero_collection` once → wire call observed
- call `get_zotero_item_metadata(key)` for an item just listed → **no wire call**
- call `get_zotero_item_metadata("UNKNOWN_KEY")` → raises `RuntimeError`

Keep all existing `MockPerspicaciteClient` tests untouched — Protocol shape is unchanged.

## Audit-doc updates

When the bridge lands:

- `2026-05-16-perspicacite-zotero-live-audit.md` Section 0 ("Wire format"): rewrite to the JSON-RPC `/mcp` streamable-http contract (initialize → session-id → `tools/call`). Replace the old plain-POST contract.
- Section 10 (curl checks): rewrite to JSON-RPC curls — show `initialize` (capture `mcp-session-id` from response header) then `tools/call` for each of the 4 tools.
- Add a note that `get_zotero_item_metadata` is satisfied **locally** from the items returned by `list_zotero_collection` (no separate wire call).
- `2026-05-16-asb-zotero-live-audit.md` Section 0 / sign-off: clarify the 4 Protocol methods are wire-mapped per this spec; ASB CLI surface (`--source-kind=zotero-mcp`, `--perspicacite-mcp`) is unchanged.

## Out of scope (call out, don't fix here)

- `search_related_papers` / `get_relevant_passages` on Perspicacité — neither exists. K3 enrichment loop will surface "Unknown tool" errors against a real server until one side adds them. Separate follow-up.
- Multi-library Perspicacité config — current server is single-library-per-config; `list_zotero_libraries` will always return a 1-element list synthesized from the configured ID. If Perspicacité ever serves a multi-library tool, the bridge can swap the synthesis call for a real list call.
- Headless-Chromium PDF bypass (P4 in the content-acquisition plan) — independent of this bridge.

## Acceptance

This bridge is complete when:

1. ASB's existing 47-test offline suite still passes unchanged (Mock client tests).
2. The 3 new tests above pass.
3. Live integration smoke: with the Perspicacité server running on `http://127.0.0.1:8765` and HolobiomicsLab/Mimosa-AI populated with 75 items + 57 PDFs (the audit state), `python3 -m agentic_science_builder build 4DNCGAD8 --source-kind zotero-mcp --perspicacite-mcp http://127.0.0.1:8765 --zotero-library-id 6555390 --output /tmp/asb_smoke` produces a package directory per item, with `biblio.json` + the OA-available PDFs from Zotero's attachments materialized in each.
4. Re-running the same command hits the cache (`zotero_cache.is_cache_hit` returns True), exit code 0, no new wire calls.

## Sequencing

ASB side is a single PR. ~250 LOC for `_MCPSession` + the rewrite of the 4 Protocol methods + ~150 LOC of tests. ETA ~1.5 days for an experienced ASB dev.

Pre-implementation checklist:
- [ ] Pull this Perspicacité commit pair (`zotero_get_attachment_bytes` tool + `attachment_keys` field).
- [ ] Confirm `tools/list` on the running server includes `zotero_get_attachment_bytes` (proves the prereqs are deployed).
- [ ] Confirm `zotero_get_collection_items` response includes `attachment_keys`.

Then write the failing test (`test_mcp_session_initialize_and_tools_call`) first, then implement `_MCPSession` to make it pass, then the 4 Protocol-method rewrites, then the 2 remaining tests.
