# MCP resources + prompts — operator guide (Wave 5.1 + 5.2)

Perspicacité's MCP server now exposes browseable KB **resources** and a
small set of canned **prompts** alongside the existing tools. Resources
let MCP clients (Claude Desktop, Cursor, etc.) discover and preview
your knowledge bases without needing to know any tool name. Prompts
give one-click entry points for the most common workflows.

Shipped 2026-05-14. Spec at
`docs/superpowers/specs/2026-05-14-mcp-resources-prompts-design.md`.

## Resources

All URIs are read-only. Missing state or unknown KBs return an error
payload — they never raise.

| URI | Payload |
|---|---|
| `perspicacite://info` | Capability flag + tool list (already shipped). |
| `perspicacite://kbs` | Index of all KBs with `paper_count` / `chunk_count`. |
| `perspicacite://kb/{name}` | Single KB metadata + sub-resource URIs. |
| `perspicacite://kb/{name}/papers` | Paper IDs + titles + chunk counts in this KB. |
| `perspicacite://kb/{name}/log` | Last N events from the per-KB append-only log. |

### `perspicacite://kbs`

```json
{
  "knowledge_bases": [
    {
      "uri": "perspicacite://kb/astro",
      "name": "astro",
      "description": "...",
      "paper_count": 47,
      "chunk_count": 812,
      "created_at": "..."
    }
  ]
}
```

### `perspicacite://kb/{name}`

```json
{
  "name": "astro",
  "description": "...",
  "paper_count": 47,
  "chunk_count": 812,
  "embedding_model": "text-embedding-3-small",
  "collection_name": "kb_astro",
  "created_at": "...",
  "updated_at": "...",
  "papers_uri": "perspicacite://kb/astro/papers",
  "log_uri":    "perspicacite://kb/astro/log"
}
```

Unknown KB → `{"error": "kb_not_found", "kb_name": "..."}`.

### `perspicacite://kb/{name}/papers`

Prefers the Wave 4.3 per-KB event log
(`data/kb_logs/{name}.jsonl`). When the log is absent or empty
(older KBs), falls back to scanning the Chroma collection for
distinct `paper_id` metadata values via
`ChromaVectorStore.list_paper_ids_in_collection`.

```json
{
  "kb_name": "astro",
  "papers": [
    {"paper_id": "10.1234/example", "title": "...", "chunks": 17}
  ]
}
```

### `perspicacite://kb/{name}/log`

Most-recent N events from the append-only log, capped at
`kb.mcp_resource_max_events` (default 1000) to keep payloads
bounded for the MCP client's context.

```json
{
  "kb_name": "astro",
  "events": [
    {"event": "paper_added", "kb_name": "astro", "paper_id": "...", "title": "...", "chunks": 17, "ts": 1}
  ]
}
```

## Prompts

Each prompt is a pure string-builder — it produces an initial
user message the model executes against the existing tool surface.

| Prompt | Args | What it does |
|---|---|---|
| `literature_review` | `topic: str`, `kb_name: str \| None = None`, `max_papers: int = 30` | Search (`search_literature` or `search_knowledge_base`) then `generate_report` with `synthesis_style="literature_review"`. |
| `compare_papers` | `paper_a: str`, `paper_b: str`, `kb_name: str \| None = None` | Fetch both via `get_paper_content`, then side-by-side table + 2-paragraph synthesis. |
| `summarize_kb` | `kb_name: str`, `max_papers: int = 50` | Broad `search_knowledge_base` + 5-paragraph summary (scope / themes / methods / gaps / next reads). |
| `ingest_dois` | `kb_name: str`, `dois: list[str]` | Call `add_dois_to_kb` then print per-DOI status. |
| `screen_topic` | `topic: str`, `kb_name: str`, `threshold: float = 0.6` | Call `screen_papers` then rank above-threshold matches. |

## Trying them in Claude Desktop

1. Make sure your Perspicacité config has `mcp.enabled: true` and
   pick a transport (`stdio` or `streamable-http`).
2. Add the server to Claude Desktop's `claude_desktop_config.json`
   (see the existing MCP onboarding docs).
3. Restart Claude Desktop. The KB resources appear under the
   server's "Resources" panel; the prompts appear in the "/"
   menu in any conversation.
4. Browse resources before chatting — e.g. open
   `perspicacite://kbs` to confirm the KB you want exists, then
   invoke the `summarize_kb` prompt from "/".

In Cursor, resources surface the same way; prompts are available
via the `@perspicacite` mention.

## Config

```yaml
knowledge_base:
  mcp_resource_max_events: 1000   # default — cap on /log resource payload
```

Lower this when your client has a tight context window or when
KBs see thousands of events per hour. The KB-log file itself is
not truncated — only the resource payload.

## Out of scope (future waves)

- MCP **sampling** (Wave 5.3) — blocked on
  `anthropics/claude-code#1785`. Adapter already in place.
- Resource **subscriptions** (clients polling for changes).
  FastMCP's notification primitives are not yet stable in 3.2.
- Multimodal prompt args (file attachments) — needs broader MCP
  client support.
