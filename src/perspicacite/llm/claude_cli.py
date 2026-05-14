"""Claude Code CLI subprocess LLM provider (Option C — subscription routing).

Spawns ``claude -p`` as a subprocess to get completions, using whatever
account the user is logged into in Claude Code. The user's Pro/Max
subscription pays for inference; no Anthropic API key is required in
Perspicacité's config.

When to choose this over the other LLM providers:

- ✅ You have a Claude subscription and want to use it for Perspicacité's
  internal LLM calls (kb_router, screening, rephrase, contextual,
  synthesis) without paying API rates on top.
- ✅ You're already using Claude Code interactively and want to share
  the same rate-limit / billing surface.
- ❌ You're running Perspicacité in production / unattended (the
  subprocess subscription is your interactive session — concurrent
  use will fight for rate limits).
- ❌ You need streaming output (this provider returns the whole
  completion at once).
- ❌ You need strict per-call temperature / max_tokens control
  (Claude Code's flags don't fully expose these for non-interactive
  use).

The cleanest path forward will be MCP sampling once Claude Code lands
client-side support — see
docs/superpowers/specs/2026-05-14-claude-code-sampling-integration-design.md.
This subprocess shim is a workable bridge until then.

**Caveat: rate limits are shared with your interactive Claude Code
session.** A heavy Perspicacité run (contextual retrieval at "chunk"
tier on a multi-paper ingest) can freeze you out of Claude Code for
hours. Use sparingly, or pair with `pdf_download.cache_pdfs` + the
existing prompt cache so re-ingest doesn't burn fresh requests.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.llm.claude_cli")


def _flatten_messages(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """Reduce a chat-style messages list to ``(system_prompt, user_prompt)``
    suitable for ``claude -p``. The CLI takes a single prompt, so we
    join non-system messages by role tag.

    Anthropic-style content blocks (list of {"type": "text", ...}) are
    flattened by concatenating their ``text`` fields and dropping the
    ``cache_control`` markers — Claude Code's CLI doesn't expose
    prompt caching anyway.
    """
    def _to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for blk in content:
                if isinstance(blk, dict):
                    parts.append(str(blk.get("text", "")))
                else:
                    parts.append(str(blk))
            return "\n".join(parts)
        return str(content)

    system_parts: list[str] = []
    body_parts: list[str] = []
    for m in messages:
        role = m.get("role")
        text = _to_text(m.get("content", ""))
        if role == "system":
            system_parts.append(text)
        elif role == "assistant":
            body_parts.append(f"[Assistant prior turn]\n{text}")
        else:  # user (or anything else)
            body_parts.append(text)
    return "\n\n".join(system_parts), "\n\n".join(body_parts)


def _resolve_model_alias(model: str | None) -> str | None:
    """Map Anthropic API model names to Claude Code aliases.

    Claude Code's ``--model`` flag accepts the short aliases (``sonnet``,
    ``haiku``, ``opus``) or full model names (``claude-sonnet-4-5-20250929``).
    Our config tends to use the short forms (e.g. ``claude-sonnet-4-5``);
    we map them to whichever form the CLI prefers without breaking
    pass-through for already-correct names.
    """
    if not model:
        return None
    m = model.lower()
    # Strip date suffix and provider prefix if present.
    if m.startswith("anthropic/"):
        m = m[len("anthropic/") :]
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    if "opus" in m:
        return "opus"
    return model


class ClaudeCLIClient:
    """LLM client that drives ``claude -p`` as a subprocess.

    Implements the same async ``complete(messages, model, provider,
    temperature, max_tokens, stage) -> str`` contract as
    :class:`AsyncLLMClient`, so it can drop into any call site that
    accepts an LLM client.
    """

    def __init__(
        self,
        *,
        executable: str = "claude",
        timeout: float = 180.0,
        cwd: str | None = None,
        env_extra: dict[str, str] | None = None,
    ):
        """
        Args:
            executable: Path or name of the ``claude`` binary.
            timeout: Per-call timeout in seconds. Heavy synthesis can
                take a while; default 3 minutes.
            cwd: Working directory for the subprocess. ``None`` =
                inherit. Some Claude Code features require a writable
                workspace; routing here avoids polluting the user's
                project.
            env_extra: Extra environment variables for the subprocess.
        """
        self.executable = executable
        self.timeout = timeout
        self.cwd = cwd
        self.env_extra = env_extra or {}

    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> str:
        """Run ``claude -p`` and return the assistant text.

        Note: ``temperature`` and ``max_tokens`` are accepted for API
        compatibility but currently ignored — Claude Code's CLI does
        not surface them in ``-p`` mode. We rely on whatever the user
        has configured.
        """
        stage = kwargs.pop("stage", "llm")
        system, body = _flatten_messages(messages)
        model_alias = _resolve_model_alias(model)

        cmd: list[str] = [
            self.executable,
            "-p",  # print mode (non-interactive)
            "--output-format", "json",  # structured result so we can parse cleanly
            "--no-session-persistence",  # don't pollute the user's session list
        ]
        if model_alias:
            cmd.extend(["--model", model_alias])
        if system:
            cmd.extend(["--append-system-prompt", system])

        env = os.environ.copy()
        env.update(self.env_extra)
        # Be polite to Anthropic's TOS: identify the agent.
        env.setdefault("CLAUDE_CODE_USER_AGENT", "Perspicacite/2.0")

        logger.info(
            "claude_cli_call_start",
            stage=stage, model_alias=model_alias,
            system_len=len(system), body_len=len(body),
        )
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=env,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"claude CLI not found at '{self.executable}'. "
                "Install Claude Code (https://claude.com/claude-code) "
                "or set llm.providers.claude_cli.executable to the "
                "absolute path."
            ) from exc

        # ``claude -p`` reads the prompt from stdin when no prompt
        # argument is supplied; safer for long prompts than CLI args.
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(body.encode("utf-8")),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RuntimeError(
                f"claude CLI timed out after {self.timeout}s"
            )
        latency_ms = (time.monotonic() - t0) * 1000.0

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace")[:500]
            raise RuntimeError(
                f"claude CLI exited {proc.returncode}: {err}"
            )

        # JSON output shape (Claude Code 2.x):
        # {"type": "result", "result": "<assistant text>", ...}
        raw = stdout.decode("utf-8", errors="replace").strip()
        try:
            payload = json.loads(raw)
            text = (
                payload.get("result")
                or payload.get("message")
                or payload.get("text")
                or raw
            )
            if not isinstance(text, str):
                text = json.dumps(text)
        except json.JSONDecodeError:
            # Some versions emit plain text — accept that too.
            text = raw

        logger.info(
            "claude_cli_call_done",
            stage=stage, latency_ms=int(latency_ms),
            output_len=len(text),
        )
        # Provenance: log to the collector if one is bound, so
        # ``perspicacite report cost`` can still account for these
        # calls (even if cost is $0 from the user's perspective).
        try:
            from perspicacite.provenance.context import get_collector
            _c = get_collector()
            if _c is not None:
                _c.add_llm_call(
                    stage_label=stage,
                    provider="claude_cli",
                    model=model_alias or "default",
                    prompt_messages=messages,
                    response_text=text,
                    prompt_tokens=0,  # unknown — CLI doesn't surface usage
                    completion_tokens=0,
                    latency_ms=latency_ms,
                )
        except Exception:
            pass
        return text
