"""MCP resource readers for KB browsing (Wave 5.1).

These functions back the ``perspicacite://kbs`` and
``perspicacite://kb/{name}[/papers|/log]`` resources. Each returns a
JSON string and never raises — failures surface as ``{"error": "..."}``
payloads so the MCP client can render a useful message.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.mcp.resources")


def _get_state() -> Any:
    """Resolve the singleton MCP state (indirected for tests)."""
    from perspicacite.mcp.server import mcp_state

    if not getattr(mcp_state, "initialized", False):
        return None
    return mcp_state


def _err(code: str, **extra: Any) -> str:
    return json.dumps({"error": code, **extra})


async def kbs_resource() -> str:
    """Resource: list of all KBs."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kbs = await state.session_store.list_kbs()
        out: list[dict[str, Any]] = []
        for kb in kbs:
            out.append(
                {
                    "uri": f"perspicacite://kb/{kb.name}",
                    "name": kb.name,
                    "description": getattr(kb, "description", None),
                    "paper_count": getattr(kb, "paper_count", 0),
                    "chunk_count": getattr(kb, "chunk_count", 0),
                    "created_at": str(getattr(kb, "created_at", "")) or None,
                }
            )
        return json.dumps({"knowledge_bases": out})
    except Exception as e:
        logger.error("mcp_resource_kbs_error", error=str(e))
        return _err("kbs_resource_failed", message=str(e))


async def kb_resource(name: str) -> str:
    """Resource: a single KB's metadata."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        return json.dumps(
            {
                "name": kb.name,
                "description": getattr(kb, "description", None),
                "paper_count": getattr(kb, "paper_count", 0),
                "chunk_count": getattr(kb, "chunk_count", 0),
                "embedding_model": getattr(kb, "embedding_model", None),
                "collection_name": getattr(kb, "collection_name", None),
                "created_at": str(getattr(kb, "created_at", "")) or None,
                "updated_at": str(getattr(kb, "updated_at", "")) or None,
                "papers_uri": f"perspicacite://kb/{name}/papers",
                "log_uri": f"perspicacite://kb/{name}/log",
            }
        )
    except Exception as e:
        logger.error("mcp_resource_kb_error", error=str(e), kb_name=name)
        return _err("kb_resource_failed", message=str(e))


def _log_path(state: Any, name: str) -> Path:
    log_dir = Path(getattr(state.config.knowledge_base, "log_dir", "data/kb_logs"))
    return log_dir / f"{name}.jsonl"


def _read_log_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    events: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            # Tolerate partial last line (Wave 4.3 contract).
            if i == len(lines) - 1:
                logger.warning("mcp_resource_log_partial_line_skipped", path=str(path))
                continue
            logger.warning("mcp_resource_log_bad_line", path=str(path), line_no=i)
    return events


async def kb_papers_resource(name: str) -> str:
    """Resource: papers in a KB. Prefers kb_log, falls back to Chroma."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        events = _read_log_lines(_log_path(state, name))
        added = [e for e in events if e.get("event") == "paper_added"]
        if added:
            papers = [
                {
                    "paper_id": e.get("paper_id"),
                    "title": e.get("title"),
                    "chunks": e.get("chunks", 0),
                }
                for e in added
            ]
            return json.dumps({"kb_name": name, "papers": papers})
        # Fallback: ask vector store.
        if state.vector_store is not None and hasattr(
            state.vector_store, "list_paper_ids_in_collection"
        ):
            rows = await state.vector_store.list_paper_ids_in_collection(
                getattr(kb, "collection_name", f"kb_{name}")
            )
            papers = [
                {"paper_id": pid, "title": title, "chunks": n}
                for (pid, title, n) in rows
            ]
            return json.dumps({"kb_name": name, "papers": papers})
        return json.dumps({"kb_name": name, "papers": []})
    except Exception as e:
        logger.error("mcp_resource_kb_papers_error", error=str(e), kb_name=name)
        return _err("kb_papers_resource_failed", message=str(e))


async def kb_log_resource(name: str) -> str:
    """Resource: the most-recent N KB-log events."""
    state = _get_state()
    if state is None:
        return _err("mcp_state_not_initialized")
    try:
        kb = await state.session_store.get_kb_metadata(name)
        if kb is None:
            return _err("kb_not_found", kb_name=name)
        events = _read_log_lines(_log_path(state, name))
        cap = int(getattr(state.config.knowledge_base, "mcp_resource_max_events", 1000))
        if len(events) > cap:
            events = events[-cap:]
        return json.dumps({"kb_name": name, "events": events})
    except Exception as e:
        logger.error("mcp_resource_kb_log_error", error=str(e), kb_name=name)
        return _err("kb_log_resource_failed", message=str(e))
