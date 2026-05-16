# tests/unit/test_zotero_mcp_new_tools.py
"""Unit tests for the 4 new Zotero MCP tools."""
from __future__ import annotations
from types import SimpleNamespace

import pytest
from perspicacite.mcp import server as mcp_server


def _zotero_cfg(enabled=True, api_key="k", library_id="42"):
    return SimpleNamespace(
        enabled=enabled, api_key=api_key, library_id=library_id,
        library_type="user", collection_key="", base_url="",
    )


def _fake_state(zotero_cfg=None):
    return SimpleNamespace(
        config=SimpleNamespace(
            zotero=zotero_cfg or _zotero_cfg(),
            pdf_download=SimpleNamespace(cache_pdfs=False, cache_dir="", unpaywall_email=""),
            capsule=SimpleNamespace(root="./data/capsules"),
        ),
        job_registry=None,
    )


def _unwrap(fn):
    return fn.fn if hasattr(fn, "fn") else fn


# --- zotero_list_collections ---

@pytest.mark.asyncio
async def test_list_collections_not_configured(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(enabled=False)))
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_list_collections_no_api_key(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(api_key="")))
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_list_collections_auth_failed(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod

    async def _bad_paginated(self, path, params=None):
        raise httpx.HTTPStatusError(
            "403", request=httpx.Request("GET", "http://x"), response=httpx.Response(403)
        )

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _bad_paginated)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert out["error"] == "ZOTERO_AUTH_FAILED"


@pytest.mark.asyncio
async def test_list_collections_returns_tree(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _fake_paginated(self, path, params=None):
        return [
            {"key": "AAA", "data": {"name": "Top", "parentCollection": False}},
            {"key": "BBB", "data": {"name": "Sub", "parentCollection": "AAA"}},
        ]

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _fake_paginated)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_list_collections)()
    assert "collections" in out
    assert len(out["collections"]) == 1  # only top-level
    assert out["collections"][0]["id"] == "AAA"
    assert out["collections"][0]["subcollections"][0]["id"] == "BBB"
