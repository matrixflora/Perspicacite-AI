# Perspicacité MCP Server — Wire Contract

The MCP server (FastMCP-based, mounted at `/mcp`) returns every tool
result as a JSON string. The envelope is stable across all tools.

## Envelope shape

Success:

````json
{"success": true, "ok": true, "<tool-specific keys>": "..."}
````

- `success` (canonical) — always present on success.
- `ok` (deprecated alias) — present for one minor cycle. Downstream
  clients should migrate to `success`. Will be removed after v3.x.

Error:

````json
{"success": false, "ok": false, "error": "<human-readable message>"}
````

## Why both keys (2026-05-15)

The Scriptorium downstream client integration found that v3.2.4 emits
`success` but earlier code used `ok`. We emit both for one cycle so
existing clients don't break during the migration.

## Latency expectations

- `search_literature` with the default 3-backend fan-out: budget
  **15-50s** per call when titles match many candidates. The
  default httpx timeout in MCP clients should be at least **60s**.
- `search_knowledge_base`: typically <1s.
- `generate_report`: 30-120s depending on KB size + LLM speed.

## Authentication

There is no auth on the MCP endpoint by default. Run behind a
reverse proxy or expose only on `localhost` in production.

## REST: `/api/llm/proxy` (added 2026-05-15)

Pure LLM gateway. No RAG, no KB. Use this when you want
Perspicacité's configured API keys + stage-tiering rules but
don't want retrieval.

```bash
curl -X POST http://localhost:5468/api/llm/proxy \
  -H "Content-Type: application/json" \
  -d '{"prompt":"What is mass spectrometry?","model":"claude-haiku-4-5"}'
```

Request body fields:

- `prompt` (required): the prompt text
- `model` (optional): override model (defaults to `llm.default_model` from config)
- `max_tokens` (optional, default 2048)
- `temperature` (optional, default 0.7)
- `stage` (optional): stage hint passed through to the LLM client
