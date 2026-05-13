import httpx
import pytest

from perspicacite.integrations.zotero import ZoteroClient


@pytest.mark.asyncio
async def test_create_item_maps_doi_and_returns_key(respx_mock):
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    create_route = respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json={
            "success": {"0": "ABC123"},
            "successful": {"0": {"key": "ABC123"}},
            "failed": {},
        })
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={
            "doi": "10.1/x", "title": "T", "year": 2024, "journal": "J",
            "authors": ["A B"], "abstract": "abs",
        })
    assert key == "ABC123"
    body_bytes = create_route.calls[0].request.read()
    assert b"10.1/x" in body_bytes
    assert b"journalArticle" in body_bytes


@pytest.mark.asyncio
async def test_dedup_returns_existing_key(respx_mock):
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[
            {"key": "EXIST", "data": {"DOI": "10.1/x", "title": "T"}}
        ])
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={"doi": "10.1/x", "title": "T"})
    assert key == "EXIST"


@pytest.mark.asyncio
async def test_group_library_uses_groups_path(respx_mock):
    create_route = respx_mock.post("https://api.zotero.org/groups/999/items").mock(
        return_value=httpx.Response(200, json={"successful": {"0": {"key": "G1"}}, "success": {"0": "G1"}, "failed": {}})
    )
    respx_mock.get("https://api.zotero.org/groups/999/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="999", library_type="group", http_client=http)
        key = await c.create_item(paper={"doi": "10.1/x", "title": "T"})
    assert key == "G1"


def test_zotero_client_requires_credentials():
    with pytest.raises(ValueError):
        ZoteroClient(api_key="", library_id="123")
    with pytest.raises(ValueError):
        ZoteroClient(api_key="k", library_id="")


@pytest.mark.asyncio
async def test_create_item_with_collection_key(respx_mock):
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    create_route = respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json={"successful": {"0": {"key": "X"}}, "success": {"0": "X"}, "failed": {}})
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user",
                         collection_key="COLL", http_client=http)
        await c.create_item(paper={"doi": "10.1/x", "title": "T"})
    body_bytes = create_route.calls[0].request.read()
    assert b"COLL" in body_bytes


@pytest.mark.asyncio
async def test_dedup_null_doi_does_not_crash_and_no_false_match(respx_mock):
    """When Zotero returns DOI: null in response, dedup must not crash and
    must not consider it a match for any real DOI."""
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "OLD", "data": {"DOI": None, "title": "Something"}}],
        )
    )
    # Provide a post mock so the item can be created without error
    respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(
            200,
            json={"successful": {"0": {"key": "NEW"}}, "success": {"0": "NEW"}, "failed": {}},
        )
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        # Should not raise AttributeError, and should NOT return "OLD"
        key = await c.create_item(paper={"doi": "10.1/real", "title": "Real"})
    # The null-DOI item must not be returned as a duplicate
    assert key != "OLD"
