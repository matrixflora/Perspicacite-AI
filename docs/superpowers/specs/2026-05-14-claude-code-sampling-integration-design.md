# Claude Code MCP Sampling Integration — Design

**Status:** Design only. Not implemented. Tracked upstream:
[anthropics/claude-code#1785](https://github.com/anthropics/claude-code/issues/1785).
Will land when Claude Code adds MCP-sampling client support.

**Goal:** When Perspicacité runs as an MCP server inside Claude Code,
route its internal LLM calls back to Claude Code via the MCP
`sampling/createMessage` protocol so the user's Claude Pro/Max
subscription pays for inference — no Anthropic API key required.

## Why this is worth scaffolding

Today, Perspicacité's MCP server hands its tools' LLM work
(`kb_router(method="llm")`, `--screen llm`, `--rephrase`, contextual
retrieval, RAG synthesis) to LiteLLM, which calls the Anthropic /
OpenAI / DeepSeek API directly with a key from `config.yml`. When a
user invokes Perspicacité tools through Claude Code, the agent's
own reasoning is on the Claude Code subscription, but every LLM call
Perspicacité makes internally is metered against the user's API key.
For a heavy run (agentic mode, contextual retrieval at "chunk" tier,
LLM screen + rephrase) that's an extra $1–10 per session on top of
the subscription cost.

MCP solves this. The protocol's
[`sampling/createMessage`](https://modelcontextprotocol.io/specification/2025-06-18/client/sampling)
lets the server *ask the client* to produce a completion. The client
(Claude Code) uses its own model + credentials. Server gets the
result back through the existing MCP transport, no extra
configuration.

`fastmcp` 3.x already implements the server side via
`Context.sample(prompt, ...)`. The block is purely client-side:
Claude Code does not currently honor sampling requests.

## Why not ship dormant scaffolding today

We considered adding the adapter behind a feature flag now so that
"the day Claude Code lands sampling, it just works." Decision: don't.
Dead code is a maintenance tax (review, tests, accidental triggers),
and the actual wiring is ~50 lines once the upstream feature exists.
This design doc + the upstream issue subscription is the right
artefact to keep around.

## Architecture (when implemented)

### 1. Adapter layer — `MCPSamplingLLMClient`

A wrapper around the existing `AsyncLLMClient` in `llm/client.py`.
Implements the same `complete(messages, model, provider, temperature,
max_tokens, **kwargs) -> str` contract so call sites don't change.

```python
class MCPSamplingLLMClient:
    def __init__(self, fallback: AsyncLLMClient):
        self.fallback = fallback

    async def complete(self, messages, model=None, provider=None, **kwargs):
        ctx = _mcp_ctx.get()  # contextvar, set by MCP tool wrapper
        if ctx is None or not config.llm.use_mcp_sampling:
            return await self.fallback.complete(
                messages, model=model, provider=provider, **kwargs,
            )
        try:
            # MCP sampling speaks single-prompt + optional system, not
            # full message list. Coalesce here.
            system = next(
                (m["content"] for m in messages if m["role"] == "system"),
                None,
            )
            user = "\n\n".join(
                m["content"] if isinstance(m["content"], str)
                else "".join(b.get("text", "") for b in m["content"])
                for m in messages if m["role"] != "system"
            )
            result = await ctx.sample(
                user,
                system_prompt=system,
                max_tokens=kwargs.get("max_tokens", 1024),
                temperature=kwargs.get("temperature", 0.0),
            )
            return result.content[0].text  # fastmcp shape
        except (ClientCapabilityError, SamplingNotSupportedError) as exc:
            # Fall through silently on capability mismatch; logged once
            logger.info("mcp_sampling_unavailable_falling_back", error=str(exc))
            return await self.fallback.complete(
                messages, model=model, provider=provider, **kwargs,
            )
```

### 2. Context propagation — `contextvars`

The MCP `Context` parameter is per-tool-invocation. We thread it
through to the LLM client without changing every internal API by
using a contextvar:

```python
# in llm/mcp_sampling.py
_mcp_ctx: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "mcp_ctx", default=None,
)

@contextmanager
def use_mcp_context(ctx):
    token = _mcp_ctx.set(ctx)
    try:
        yield
    finally:
        _mcp_ctx.reset(token)
```

### 3. MCP tool wrapping

The handful of MCP tools that drive LLM-heavy work accept the fastmcp
`Context` parameter and set the contextvar before delegating to the
orchestrator. Example:

```python
@mcp.tool()
async def generate_report(
    query: str,
    kb_name: str,
    ctx: Context | None = None,
) -> str:
    with use_mcp_context(ctx):
        # existing logic; LLM calls inside this block will sample
        return await _generate_report_impl(...)
```

Tools that don't trigger LLM work (`list_knowledge_bases`,
`get_paper_content`, `fetch_supplementary`, etc.) don't need the ctx
parameter.

### 4. Config

```yaml
llm:
  default_provider: "anthropic"
  default_model: "claude-sonnet-4-5"
  use_mcp_sampling: true       # NEW — opt-in until upstream is stable
  sampling_fallback_provider: "anthropic"  # used when sampling fails
```

`use_mcp_sampling: false` is the safe default until Claude Code's
sampling implementation has been exercised in production.

### 5. Call-site scope

| Site | Subscription via sampling? |
|------|----------------------------|
| MCP tool → kb_router LLM | ✓ |
| MCP tool → screen_papers LLM | ✓ |
| MCP tool → rephrase_query | ✓ |
| MCP tool → contextual retrieval | ✓ |
| MCP tool → RAG synthesis (basic, advanced, profound, agentic) | ✓ |
| REST API `/api/chat` → all LLM | ✗ (no ctx) |
| CLI `query` / `search-to-kb --screen llm` | ✗ (no ctx) |
| Background jobs (BibTeX ingest with contextual retrieval) | ✗ (no ctx) |

The CLI / REST paths keep using LiteLLM with the configured provider
because there is no MCP client to delegate to. This is fine — the
heavy *interactive* work is what users care about cost-wise.

## What blocks implementation today

- **Claude Code CLI** must implement MCP `sampling/createMessage` as a
  client. Tracked at
  [anthropics/claude-code#1785](https://github.com/anthropics/claude-code/issues/1785).
- Codex CLI — same gap, no public tracker.
- Claude Desktop reportedly has partial sampling support; could be the
  initial test target if the user runs Perspicacité from Claude
  Desktop instead of Claude Code.

## Cost-saving levers we *can* ship today (companion work)

The companion commit (this same day) adds **Anthropic prompt caching**
to the two highest-traffic LLM call sites (`kb_router` LLM mode and
contextual retrieval). Prompt caching gives a 90% discount on cached
prefix tokens with a 5-minute TTL — directly addresses the
"contextual retrieval is expensive per-chunk" complaint without
touching the protocol question.

Other zero-protocol levers documented in the README:
- `default_model: "claude-haiku-4-5"` for synthesis when Sonnet quality
  isn't needed (config-only change; multi-LLM flexibility preserved).
- Ollama provider: zero per-call cost on local model (config-only).

## Implementation checklist (when unblocked)

- [ ] Add `LLMConfig.use_mcp_sampling` + `sampling_fallback_provider`.
- [ ] New module `src/perspicacite/llm/mcp_sampling.py` with adapter,
      contextvar, `use_mcp_context` context manager.
- [ ] Wrap LLM-heavy MCP tools (~6 sites) with `ctx: Context = None`
      param + `use_mcp_context(ctx)` block.
- [ ] Update `AppState.llm_client` initialisation to wrap fallback
      client in `MCPSamplingLLMClient` when `use_mcp_sampling=True`.
- [ ] Unit test: contextvar isolation across concurrent tool calls.
- [ ] Integration test against Claude Desktop (whoever lands sampling
      first) to verify the round-trip.
- [ ] README section + config example.

Estimated effort once upstream lands: 1 day.
