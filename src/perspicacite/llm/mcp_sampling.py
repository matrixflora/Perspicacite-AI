"""MCP sampling adapter (Option D — protocol-native subscription routing).

When Perspicacité runs as an MCP server and the connected MCP client
implements ``sampling/createMessage``, this module lets internal LLM
calls flow back to the client's model + credentials instead of using
Perspicacité's configured Anthropic / OpenAI / DeepSeek API key.

Today's client landscape (May 2026):

- **Claude Code CLI** — does NOT implement sampling
  (anthropics/claude-code#1785, still open). Falls back to LiteLLM.
- **Claude Desktop** — partial support; works for some tool calls.
- **Cursor / Cline / community clients** — varying support; opt-in
  per-client.

So this adapter ships *live*, not dormant: where the client supports
it, you get free inference today; where it doesn't, the fallback path
makes the call invisible to users. Whoever lands sampling first wins.

The companion design doc
(``docs/superpowers/specs/2026-05-14-claude-code-sampling-integration-design.md``)
explains why this exists and the long-term goals.

Architecture:

- :func:`use_mcp_context` — context manager that binds a fastmcp
  ``Context`` into a :class:`contextvars.ContextVar` for the duration
  of an MCP tool invocation. Set at the tool boundary so the LLM
  call sites don't need to thread ``ctx`` through five orchestrator
  layers.
- :func:`current_mcp_context` — peek at the currently-bound ctx.
  Returns ``None`` outside an MCP tool call (e.g. CLI / REST paths).
- :class:`SamplingLLMAdapter` — wrapper that ``AsyncLLMClient``
  delegates to before falling through to LiteLLM. Try the sampling
  call; on ``ClientCapability`` errors, fall back transparently.

The adapter is intentionally NOT a separate provider in the registry.
Sampling is orthogonal to "which model" — the client picks the model,
not us — so it lives behind a single ``llm.use_mcp_sampling: bool``
flag and applies uniformly.
"""

from __future__ import annotations

import contextlib
import contextvars
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.llm.mcp_sampling")


_mcp_ctx: contextvars.ContextVar[Any | None] = contextvars.ContextVar(
    "perspicacite_mcp_ctx", default=None,
)


def current_mcp_context() -> Any | None:
    """Return the MCP ``Context`` currently bound, or ``None``."""
    return _mcp_ctx.get()


@contextlib.contextmanager
def use_mcp_context(ctx: Any):
    """Bind ``ctx`` for the duration of the ``with`` block.

    Use at the top of MCP tool bodies::

        @mcp.tool()
        async def generate_report(..., ctx: Context = None):
            with use_mcp_context(ctx):
                # any nested llm_client.complete(...) call can sample
                ...

    Safe to pass ``None`` — the contextvar simply doesn't change, and
    sampling is silently disabled for the block.
    """
    if ctx is None:
        yield
        return
    token = _mcp_ctx.set(ctx)
    try:
        yield
    finally:
        _mcp_ctx.reset(token)


def _flatten_to_sampling(messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    """Flatten chat-style messages into ``(system, prompt)`` pair.

    MCP sampling speaks single-prompt-plus-optional-system, not full
    multi-turn message lists. We concatenate non-system messages by
    role tag — mirrors what the Claude Code CLI provider does.

    Anthropic-style content blocks (``[{"type": "text", "text": ...}]``)
    are flattened by concatenating the ``text`` fields; ``cache_control``
    markers are discarded because sampling clients don't expose caching.
    """
    def _to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                str(b.get("text", "")) if isinstance(b, dict) else str(b)
                for b in content
            )
        return str(content)

    system_parts: list[str] = []
    body_parts: list[str] = []
    for m in messages:
        text = _to_text(m.get("content", ""))
        role = m.get("role")
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            body_parts.append(f"[Assistant prior turn]\n{text}")
        else:
            body_parts.append(text)
    return (
        ("\n\n".join(system_parts) or None),
        "\n\n".join(body_parts),
    )


async def try_sample(
    *,
    messages: list[dict[str, Any]],
    temperature: float = 0.0,
    max_tokens: int = 1024,
) -> str | None:
    """Attempt an MCP sampling call against the current ctx.

    Returns the assistant text on success, or ``None`` when:

    - No MCP ctx is bound (i.e., not inside a tool that wrapped its
      body in :func:`use_mcp_context`).
    - The client doesn't advertise sampling capability.
    - The sampling call raises any error.

    Callers (typically :class:`AsyncLLMClient`) fall back to LiteLLM
    on ``None``. Errors are logged at INFO level — sampling failures
    are expected on non-supporting clients and shouldn't be alarming.
    """
    ctx = current_mcp_context()
    if ctx is None:
        return None
    system, body = _flatten_to_sampling(messages)
    try:
        # fastmcp 3.x signature: Context.sample(messages, system_prompt,
        # temperature, max_tokens, ...) — returns a content block list.
        # Be liberal in what we accept here because the upstream shape
        # has churned a bit.
        sample_kwargs: dict[str, Any] = {
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            sample_kwargs["system_prompt"] = system
        result = await ctx.sample(body, **sample_kwargs)
    except Exception as exc:
        logger.info(
            "mcp_sampling_unavailable",
            error=str(exc)[:200],
            error_type=type(exc).__name__,
        )
        return None
    # Extract text. fastmcp returns either a `TextContent` object,
    # a string, or a list of content blocks depending on version.
    try:
        if isinstance(result, str):
            return result
        # TextContent / similar with .text attribute
        text = getattr(result, "text", None)
        if isinstance(text, str):
            return text
        # List of content blocks
        if isinstance(result, list):
            parts: list[str] = []
            for blk in result:
                t = getattr(blk, "text", None) or (
                    blk.get("text") if isinstance(blk, dict) else None
                )
                if t:
                    parts.append(str(t))
            if parts:
                return "\n".join(parts)
        # Fallback — dict with 'content' / 'text' / 'message'
        if isinstance(result, dict):
            for k in ("text", "content", "message", "result"):
                v = result.get(k)
                if isinstance(v, str):
                    return v
    except Exception as exc:
        logger.info("mcp_sampling_parse_failed", error=str(exc))
    return None
