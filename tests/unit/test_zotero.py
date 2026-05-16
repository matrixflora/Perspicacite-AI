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


@pytest.mark.asyncio
async def test_dedup_falls_back_to_recent_items_when_search_misses(respx_mock):
    """Zotero's full-text search index is eventually consistent; items
    pushed via the API can take minutes-to-hours to become searchable.
    During that window the q=<DOI> search returns []. Dedup must fall
    back to scanning recent items so we don't double-create.

    Regression for 2026-05-16 audit: pushing 10.48550/arxiv.2603.08127
    twice within 15 minutes produced two Zotero items because the
    second push's q=<DOI> search hadn't yet indexed the first.
    """
    # Search by ?q=... returns empty (indexing lag) — match the first GET only.
    # The second GET hits the same /items endpoint with different params
    # (direction=desc, limit=100) and returns the actual item.
    calls_by_q: list[str | None] = []

    def _handler(request):
        q = request.url.params.get("q")
        direction = request.url.params.get("direction")
        calls_by_q.append(q)
        if q:
            # Indexing-lag path: search returns nothing
            return httpx.Response(200, json=[])
        if direction == "desc":
            # Fallback: recent items includes the duplicate
            return httpx.Response(200, json=[
                {"key": "RECENT_DUP",
                 "data": {"DOI": "10.48550/arxiv.2603.08127",
                          "title": "EvoScientist"}}
            ])
        return httpx.Response(200, json=[])

    respx_mock.get("https://api.zotero.org/groups/6555390/items").mock(side_effect=_handler)
    respx_mock.post("https://api.zotero.org/groups/6555390/items").mock(
        return_value=httpx.Response(200, json={
            "successful": {"0": {"key": "WOULDVE_BEEN_DUP"}},
            "success": {"0": "WOULDVE_BEEN_DUP"},
            "failed": {},
        })
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="6555390", library_type="group", http_client=http)
        key = await c.create_item(paper={
            "doi": "10.48550/arxiv.2603.08127",
            "title": "EvoScientist",
        })
    assert key == "RECENT_DUP", (
        f"dedup should have found RECENT_DUP via recent-items fallback, got {key!r}; "
        f"GET calls: {calls_by_q}"
    )


@pytest.mark.asyncio
async def test_dedup_normalizes_doi_prefix_and_case(respx_mock):
    """DOI normalization must strip https://doi.org/ prefix and be
    case-insensitive so https://doi.org/10.1038/X matches 10.1038/x."""
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[
            {"key": "MATCH",
             "data": {"DOI": "https://doi.org/10.1038/X", "title": "T"}}
        ])
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={"doi": "10.1038/x", "title": "T"})
    assert key == "MATCH"


@pytest.mark.asyncio
async def test_create_item_url_route_creates_webpage(respx_mock):
    """When paper has no DOI but has a URL + title, create a webpage item."""
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    create = respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json={
            "successful": {"0": {"key": "WP1"}},
            "success": {"0": "WP1"},
            "failed": {},
        })
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={
            "url": "https://github.com/langchain-ai/langgraph",
            "title": "LangGraph",
            "authors": ["LangChain Inc."],
        })
    assert key == "WP1"
    body = create.calls[0].request.read()
    assert b"webpage" in body
    assert b"langgraph" in body.lower()


@pytest.mark.asyncio
async def test_create_item_url_route_dedups_by_url(respx_mock):
    """URL-route items dedup by URL when the DOI is empty."""
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[
            {"key": "EXISTING_URL_ITEM",
             "data": {"itemType": "webpage",
                       "url": "https://github.com/langchain-ai/langgraph",
                       "title": "LangGraph"}}
        ])
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        key = await c.create_item(paper={
            "url": "https://github.com/langchain-ai/langgraph",
            "title": "LangGraph",
        })
    assert key == "EXISTING_URL_ITEM"


@pytest.mark.asyncio
async def test_create_item_explicit_item_type(respx_mock):
    """Caller can override item_type — e.g. for preprints or software."""
    respx_mock.get("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json=[])
    )
    create = respx_mock.post("https://api.zotero.org/users/123/items").mock(
        return_value=httpx.Response(200, json={
            "successful": {"0": {"key": "PRE1"}},
            "success": {"0": "PRE1"},
            "failed": {},
        })
    )
    async with httpx.AsyncClient() as http:
        c = ZoteroClient(api_key="k", library_id="123", library_type="user", http_client=http)
        await c.create_item(paper={
            "doi": "10.48550/arXiv.1234",
            "title": "Foo",
            "item_type": "preprint",
            "repository": "arXiv",
            "archive_id": "1234.5678",
        })
    body = create.calls[0].request.read()
    assert b"preprint" in body
    assert b"arXiv" in body
