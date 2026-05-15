# MCP resources + prompts — design spec

**Waves 5.1 + 5.2 of `docs/roadmap-2026-05-followups.md`.**

**Goal:** Surface KB inventory + per-KB views as MCP **resources**
(browsable / previewable, not just callable), and ship a small set of
canned **prompts** to cut UX friction in Claude Desktop / Cursor.

(Wave 5.3 — MCP sampling retest — is deferred until
`anthropics/claude-code#1785` lands; the adapter is already in place.)

## Why now

Today every Perspicacité capability is exposed as a *tool* in MCP.
Tools are imperative — the client has to know to call
`list_knowledge_bases` and then `search_knowledge_base`. Resources
are declarative: the client can discover KBs by browsing the
resource tree, preview a KB's metadata before deciding to query it,
and link back to specific papers.

Prompts let Claude Desktop's "/" menu surface canned workflows like
"literature review on X" or "compare paper A vs paper B" without the
user having to remember the exact tool sequence.

## 5.1 — KBs as MCP resources

### Resource URIs

```
perspicacite://info                   (already shipped — capability flag)
perspicacite://kbs                    (NEW — index of all KBs)
perspicacite://kb/{name}              (NEW — single KB metadata + summary)
perspicacite://kb/{name}/papers       (NEW — paper IDs + titles in this KB)
perspicacite://kb/{name}/log          (NEW — append-only event log from Wave 4.3)
```

### Payload shapes

`perspicacite://kbs`:

```json
{
  "knowledge_bases": [
    {
      "uri": "perspicacite://kb/astro",
      "name": "astro",
      "description": "...",
      "paper_count": 47,
      "chunk_count": 812,
      "created_at": "2026-05-12T10:23:01Z"
    }, ...
  ]
}
```

`perspicacite://kb/{name}`:

```json
{
  "name": "astro",
  "description": "...",
  "paper_count": 47,
  "chunk_count": 812,
  "embedding_model": "all-MiniLM-L6-v2",
  "collection_name": "kb_astro",
  "created_at": "...",
  "updated_at": "...",
  "papers_uri": "perspicacite://kb/astro/papers",
  "log_uri":    "perspicacite://kb/astro/log"
}
```

Returns a 404-style error payload when the KB does not exist.

`perspicacite://kb/{name}/papers`:

```json
{
  "kb_name": "astro",
  "papers": [
    {"paper_id": "10.1234/example", "title": "...", "chunks": 17},
    ...
  ]
}
```

Source: read `paper_added` events from `data/kb_logs/{name}.jsonl`
(Wave 4.3). Fall back to scanning the Chroma collection for distinct
`paper_id` metadata values when the log is empty (older KBs).

`perspicacite://kb/{name}/log`:

```json
{
  "kb_name": "astro",
  "events": [ {KBEvent JSON}, ... ]
}
```

Reads the raw `kb_log.jsonl`. Bounded at 1000 most-recent events
(configurable via `kb.mcp_resource_max_events: int = 1000`) so the
payload doesn't blow up Claude Desktop's context.

### Implementation notes

- Use `@mcp.resource("perspicacite://kb/{name}")` (FastMCP 3.x
  supports template URIs).
- All resource readers go through the same `MCPState` as tools — no
  duplicated init.
- A missing or uninitialised state returns an error payload, never
  raises.
- Resources are read-only. State mutations stay on the tool layer.

## 5.2 — MCP prompts (canned workflows)

### Prompts

Each prompt returns a `Message[]` array — the FastMCP convention is
a list of `{"role": "user", "content": "..."}` blocks. Claude
Desktop renders them as the starting message of a new conversation.

| Prompt | Args | Output |
|---|---|---|
| `literature_review` | `topic: str`, `kb_name: str \| None = None`, `max_papers: int = 30` | Asks the model to use `search_literature` (if no KB) or `search_knowledge_base` (if KB given), then `generate_report` with synthesis style "literature review". |
| `compare_papers` | `paper_a: str`, `paper_b: str`, `kb_name: str \| None = None` | Fetches both papers via `get_paper_content` and asks for a side-by-side comparison covering methods, findings, limitations. |
| `summarize_kb` | `kb_name: str`, `max_papers: int = 50` | Asks for a 5-paragraph summary of the KB: scope, top themes, methodological trends, gaps, suggested next reads. |
| `ingest_dois` | `kb_name: str`, `dois: list[str]` | Walks `add_dois_to_kb` then prints a per-DOI status summary. |
| `screen_topic` | `topic: str`, `kb_name: str`, `threshold: float = 0.6` | Calls `screen_papers` against the KB for the given topic and reports above-threshold matches. |

### Implementation notes

- Use `@mcp.prompt()` decorators alongside the existing
  `@mcp.tool()` decorators in `server.py`.
- Each prompt is pure string-building — no I/O. The actual work
  happens when the model executes the tool calls embedded in the
  generated message.
- Keep the prompt body short (~10 lines each) and instructive. The
  model is doing the work; the prompt just kicks it off.

## Components

| File | Change |
|---|---|
| `src/perspicacite/mcp/resources.py` (new) | KB-resource readers (5 functions). Imports `mcp_state` from `server.py`. |
| `src/perspicacite/mcp/prompts.py` (new) | The 5 prompt definitions. |
| `src/perspicacite/mcp/server.py` | Import + register both modules. |
| `src/perspicacite/config/schema.py` | Add `kb.mcp_resource_max_events: int = 1000`. |
| `tests/unit/test_mcp_resources.py` (new) | Each resource reader returns the right shape for a populated KB, a missing KB, an empty log. |
| `tests/unit/test_mcp_prompts.py` (new) | Each prompt returns a non-empty Message list with the args interpolated. |
| `docs/mcp-resources-prompts-2026-05-14.md` (new) | Operator guide. |

## Behaviour contract

- `mcp_state` not initialised → resource returns a friendly error
  payload (`{"error": "mcp_state_not_initialized"}`), never raises.
- KB does not exist → `{"error": "kb_not_found", "kb_name": "..."}`.
- KB log file missing → `papers` resource falls back to Chroma; `log`
  resource returns `{"events": []}`.
- Prompts never raise; if args are missing the prompt's docstring
  surfaces in the MCP-client UI and asks for the missing arg.

## Test plan

- `test_kbs_resource_lists_all`
- `test_kb_resource_returns_metadata_with_subresource_uris`
- `test_kb_resource_missing_returns_error_payload`
- `test_kb_papers_resource_reads_from_log_when_available`
- `test_kb_papers_resource_falls_back_to_chroma_when_log_empty`
- `test_kb_log_resource_bounded_at_max_events`
- `test_literature_review_prompt_interpolates_args`
- `test_compare_papers_prompt_includes_both_ids`
- `test_summarize_kb_prompt_requires_kb_name`
- `test_ingest_dois_prompt_renders_doi_list`
- `test_screen_topic_prompt_threshold_appears_in_body`

## Out of scope (followups)

- MCP sampling (Wave 5.3, blocked upstream).
- Resource subscriptions (clients polling for changes). FastMCP's
  notification primitives are not yet stable in 3.2.
- Prompts that take file-attachment args (would require client
  support for `MultimodalPromptArg` — defer).
