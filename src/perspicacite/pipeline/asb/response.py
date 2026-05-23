"""Response-time helper: derive skill_metadata + workflow_metadata
from a list of chunk dicts returned by the retrieval layer.

Used by the chat router and MCP response builders to surface
ASB-derived structured metadata back to the calling client.
Pure function — no I/O. Idempotent. Safe to call on mixed-source
chunk lists (non-ASB chunks are ignored).
"""
from __future__ import annotations

from typing import Any


def build_asb_response_metadata(chunks: list[dict[str, Any]]) -> dict[str, list]:
    """Group ASB-sourced chunks into skill / workflow summary blocks.

    Args:
        chunks: list of chunk dicts. Each *may* have a ``metadata``
            mapping; chunks without ``content_kind`` or without
            ``metadata`` are ignored.

    Returns:
        ``{"skill_metadata": [...], "workflow_metadata": [...]}``.
        Each list deduplicates by skill_id / task_id (first wins).
    """
    skill_map: dict[str, dict] = {}
    workflow_map: dict[str, dict] = {}

    for chunk in chunks:
        # Defensive: callers (chat router, MCP tool returns) construct
        # the helper input from streamed source dicts whose `metadata`
        # field is *usually* dict-or-None — but a malformed upstream
        # (e.g. SimpleNamespace, bare string) must not crash the
        # helper. Skip non-dict metadata silently.
        md = chunk.get("metadata") if isinstance(chunk, dict) else None
        if not isinstance(md, dict):
            continue
        kind = md.get("content_kind")
        if not kind:
            continue
        if kind.startswith("skill_"):
            skill_id = md.get("skill_id")
            if not skill_id or skill_id in skill_map:
                continue
            raw_tools = md.get("tools") or []
            # Each tool entry must be a dict; bare strings / other shapes
            # are dropped (same defensive contract as `metadata`).
            tools = [t for t in raw_tools if isinstance(t, dict)]
            env = md.get("environment") or []
            params = md.get("parameters") or []
            # Executable iff every tool has BOTH canonical_url and install
            executable = bool(tools) and all(
                bool(t.get("canonical_url")) and bool(t.get("install"))
                for t in tools
            )
            skill_map[skill_id] = {
                "skill_id": skill_id,
                "skill_name": md.get("skill_name"),
                "tool_requirements": [
                    {
                        "name": t.get("name"),
                        "canonical_url": t.get("canonical_url"),
                        "install": t.get("install"),
                    }
                    for t in tools
                ],
                "environment": list(env),
                "parameters": list(params),
                "executable": executable,
                "asb_mcp_hint": f"asb://skill/{skill_id}",
            }
        elif kind == "workflow_card":
            task_id = md.get("task_id")
            if not task_id or task_id in workflow_map:
                continue
            workflow_map[task_id] = {
                "task_id": task_id,
                "title": md.get("task_card_title"),
                # 2026-05-16 task_objective; absent on 2026-05-15 cards
                "task_objective": md.get("task_objective"),
                "domain": md.get("domain"),
                "skills_used": list(md.get("skills_used") or []),
                "tools_used": list(md.get("tools_used") or []),
                "parameters": list(md.get("parameters") or []),
                "expected_outputs": list(md.get("expected_outputs") or []),
                "evaluation_strategy": dict(md.get("evaluation_strategy") or {}),
                "paper_doi": md.get("paper_doi"),
                "paper_github": md.get("paper_github"),
                "downstream_tasks": list(md.get("downstream_tasks") or []),
                "upstream_tasks": list(md.get("upstream_tasks") or []),
                # 2026-05-16 execution fields (None / default when absent)
                "executable": md.get("executable"),
                "execution_profile": dict(md.get("execution_profile") or {}),
                "task_inputs": list(md.get("task_inputs") or []),
                "task_outputs": list(md.get("task_outputs") or []),
                "expected_artifact_name": md.get("expected_artifact_name"),
                "run_timeout_seconds": md.get("run_timeout_seconds"),
                "reproducibility_tier": md.get("reproducibility_tier"),
            }

    return {
        "skill_metadata": list(skill_map.values()),
        "workflow_metadata": list(workflow_map.values()),
    }
