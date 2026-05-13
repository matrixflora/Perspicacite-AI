"""MCP ingest_local_documents tool — refuses without allow-list, works with one."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_refuses_without_allowed_roots(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(local_docs=SimpleNamespace(allowed_roots=[])),
    ))
    fn = mcp_server.ingest_local_documents
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(kb_name="x", paths=["/etc/hosts"])
    assert "error" in out


@pytest.mark.asyncio
async def test_works_with_allow_list(tmp_path, monkeypatch):
    f = tmp_path / "doc.md"
    f.write_text("# x")
    captured: dict = {}

    async def _ingest(**kwargs):
        captured.update(kwargs)
        return {"added_chunks": 1, "files": 1}

    monkeypatch.setattr("perspicacite.integrations.local_docs.ingest_local_documents", _ingest)
    monkeypatch.setattr(mcp_server, "mcp_state", SimpleNamespace(
        config=SimpleNamespace(local_docs=SimpleNamespace(allowed_roots=[tmp_path])),
        job_registry=SimpleNamespace(create=AsyncMock(return_value="J1")),
    ))
    fn = mcp_server.ingest_local_documents
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(kb_name="x", paths=[str(f)])
    assert out.get("added_chunks") == 1


@pytest.mark.asyncio
async def test_get_info_lists_thirteen_tools():
    raw = await mcp_server.get_info()
    info = json.loads(raw)
    assert info["tool_count"] >= 13
    assert "ingest_local_documents" in info["tools"]
