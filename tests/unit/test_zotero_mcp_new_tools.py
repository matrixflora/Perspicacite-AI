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


# --- zotero_get_collection_items ---

@pytest.mark.asyncio
async def test_get_collection_items_collection_not_found(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod

    async def _bad(self, path, params=None):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
        )

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _bad)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_get_collection_items)(collection_id="MISSING")
    assert out["error"] == "COLLECTION_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_collection_items_returns_items_with_license(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod

    async def _fake_items(self, coll_key, *, include_subcollections=True):
        return [{
            "key": "ITEM1",
            "data": {
                "DOI": "10.1234/open",
                "title": "Open Paper",
                "creators": [{"firstName": "A", "lastName": "Smith"}],
                "date": "2024",
                "abstractNote": "Abstract text",
                "itemType": "journalArticle",
                "tags": [{"tag": "open-access"}],
            }
        }]

    async def _fake_classify(self, doi, *, zotero_item=None, http_client=None, **kw):
        from perspicacite.integrations.zotero_license import LicenseInfo
        return LicenseInfo(spdx="CC-BY-4.0", classification="permissive", policy="verbatim", source="crossref")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "list_items_in_collection", _fake_items)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    out = await _unwrap(mcp_server.zotero_get_collection_items)(collection_id="AAA")
    assert "items" in out
    assert len(out["items"]) == 1
    item = out["items"][0]
    assert item["doi"] == "10.1234/open"
    assert item["license"]["classification"] == "permissive"
    assert item["license"]["policy"] == "verbatim"
    assert item["has_attachments"] is False
    assert out["next_cursor"] is None


@pytest.mark.asyncio
async def test_get_collection_items_cursor_pagination(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod
    from perspicacite.integrations.zotero_license import LicenseInfo

    all_items = [
        {"key": f"I{i}", "data": {"DOI": f"10.0/{i}", "title": f"T{i}", "creators": [],
          "date": "2024", "abstractNote": "", "itemType": "journalArticle", "tags": []}}
        for i in range(3)
    ]

    async def _fake_items(self, coll_key, *, include_subcollections=True):
        return all_items

    async def _fake_classify(self, doi, **kw):
        return LicenseInfo(spdx=None, classification="unknown", policy="reflavor", source="unknown")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "list_items_in_collection", _fake_items)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    page1 = await _unwrap(mcp_server.zotero_get_collection_items)(
        collection_id="AAA", limit=2
    )
    assert len(page1["items"]) == 2
    assert page1["next_cursor"] is not None

    page2 = await _unwrap(mcp_server.zotero_get_collection_items)(
        collection_id="AAA", limit=2, cursor=page1["next_cursor"]
    )
    assert len(page2["items"]) == 1
    assert page2["next_cursor"] is None


# --- zotero_get_paper_resources ---

@pytest.mark.asyncio
async def test_get_paper_resources_paper_not_found(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _paginated_empty(self, path, params=None):
        return []

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _paginated_empty)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.9999/missing")
    assert out["error"] == "PAPER_NOT_FOUND"


@pytest.mark.asyncio
async def test_get_paper_resources_ambiguous_doi(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod

    async def _two_items(self, path, params=None):
        return [
            {"key": "K1", "data": {"DOI": "10.1234/x", "title": "T1"}},
            {"key": "K2", "data": {"DOI": "10.1234/x", "title": "T2"}},
        ]

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _two_items)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.1234/x")
    assert out["error"] == "AMBIGUOUS_DOI"


@pytest.mark.asyncio
async def test_get_paper_resources_returns_resources(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_license as lic_mod
    from perspicacite.integrations.zotero_license import LicenseInfo

    async def _one_item(self, path, params=None):
        return [{"key": "K1", "data": {"DOI": "10.1234/y", "title": "T"}}]

    async def _no_attachments(self, item_key):
        return []

    async def _fake_classify(self, doi, **kw):
        return LicenseInfo("CC-BY-4.0", "permissive", "verbatim", "crossref")

    monkeypatch.setattr(zotero_mod.ZoteroClient, "_paginated", _one_item)
    monkeypatch.setattr(zotero_mod.ZoteroClient, "get_item_attachments", _no_attachments)
    monkeypatch.setattr(lic_mod.LicenseClassifier, "classify", _fake_classify)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    out = await _unwrap(mcp_server.zotero_get_paper_resources)(doi="10.1234/y")
    assert out.get("doi") == "10.1234/y"
    assert "resources" in out
    assert out["license"]["classification"] == "permissive"
    pdf = next(r for r in out["resources"] if r["role"] == "fulltext_pdf")
    doi_access = [a for a in pdf["access"] if a.get("via") == "doi_resolver"]
    assert len(doi_access) == 1


# --- zotero_ingest_collection_to_kb ---

@pytest.mark.asyncio
async def test_ingest_collection_not_configured(monkeypatch):
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state(_zotero_cfg(enabled=False)))
    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(collection_id="AAA")
    assert out["error"] == "ZOTERO_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_ingest_collection_inline_mode(monkeypatch):
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_ingest as zi

    async def _fake_plan(client, *, top_level_collection_keys=None, **kw):
        return [
            zi.ZoteroKBPlanEntry(
                kb_name="metabolomics",
                source_collection_key="AAA",
                source_collection_name="Metabolomics",
                item_count=5,
                with_doi_count=5,
                with_pdf_count=0,
                with_notes_count=0,
            )
        ]

    async def _fake_build(client, *, plan, app_state, registry, job_id):
        await registry.finish(job_id, {"per_kb": [{"kb": "metabolomics", "papers": 5}]})

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _fake_plan)
    monkeypatch.setattr(zi, "build_kbs_from_zotero", _fake_build)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())

    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(
        collection_id="AAA", kb_name="metabolomics"
    )
    # Inline mode returns the result directly (no job_id)
    assert "per_kb" in out or "job_id" in out


@pytest.mark.asyncio
async def test_ingest_collection_not_found(monkeypatch):
    import httpx
    from perspicacite.integrations import zotero as zotero_mod
    from perspicacite.integrations import zotero_ingest as zi

    async def _bad_plan(client, **kw):
        raise httpx.HTTPStatusError(
            "404", request=httpx.Request("GET", "http://x"), response=httpx.Response(404)
        )

    monkeypatch.setattr(zi, "plan_kbs_from_zotero", _bad_plan)
    monkeypatch.setattr(mcp_server, "mcp_state", _fake_state())
    out = await _unwrap(mcp_server.zotero_ingest_collection_to_kb)(collection_id="NOPE")
    assert out["error"] == "COLLECTION_NOT_FOUND"
