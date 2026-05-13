"""MCP build_kbs_from_zotero tool (plan-only + execute paths)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from perspicacite.mcp import server as mcp_server


@pytest.mark.asyncio
async def test_build_kbs_from_zotero_plan_only(monkeypatch):
    from perspicacite.integrations import zotero_ingest as zi

    async def _plan(*a, **k):
        return [
            zi.ZoteroKBPlanEntry(
                kb_name="X",
                source_collection_key=None,
                source_collection_name=None,
                item_count=1,
                with_doi_count=1,
                with_pdf_count=0,
                with_notes_count=0,
            )
        ]

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _plan)
    fake_state = SimpleNamespace(
        config=SimpleNamespace(
            zotero=SimpleNamespace(
                enabled=True,
                api_key="k",
                library_id="42",
                library_type="user",
                collection_key="",
            )
        ),
    )
    monkeypatch.setattr(mcp_server, "mcp_state", fake_state)
    fn = mcp_server.build_kbs_from_zotero
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(plan_only=True)
    assert "plan" in out
    assert out["plan"][0]["kb_name"] == "X"


@pytest.mark.asyncio
async def test_build_kbs_from_zotero_refuses_when_zotero_disabled(monkeypatch):
    fake_state = SimpleNamespace(
        config=SimpleNamespace(
            zotero=SimpleNamespace(
                enabled=False,
                api_key="",
                library_id="",
                library_type="user",
                collection_key="",
            )
        ),
    )
    monkeypatch.setattr(mcp_server, "mcp_state", fake_state)
    fn = mcp_server.build_kbs_from_zotero
    if hasattr(fn, "fn"):
        fn = fn.fn
    out = await fn(plan_only=True)
    assert "error" in out


@pytest.mark.asyncio
async def test_get_info_lists_twelve_tools():
    raw = await mcp_server.get_info()
    info = json.loads(raw)
    assert info["tool_count"] >= 12
    assert "build_kbs_from_zotero" in info["tools"]
