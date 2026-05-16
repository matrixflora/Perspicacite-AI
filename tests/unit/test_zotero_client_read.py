"""ZoteroClient read methods + HTML-to-text helper."""

from __future__ import annotations

import httpx
import pytest

from perspicacite.integrations.zotero import ZoteroClient, _html_to_text


def test_html_to_text_strips_tags_keeps_text():
    html = "<p><b>Title</b></p><ul><li>one</li><li>two</li></ul>"
    out = _html_to_text(html)
    assert "Title" in out
    assert "one" in out
    assert "two" in out
    assert "<" not in out


@pytest.mark.asyncio
async def test_list_collections_paginates():
    pages = [
        [{"key": "C1", "data": {"name": "Coll1"}}],
        [{"key": "C2", "data": {"name": "Coll2"}}],
        [],
    ]
    calls = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[calls["i"]]
        calls["i"] += 1
        return httpx.Response(200, json=body)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.list_collections()
    assert [x["key"] for x in out] == ["C1", "C2"]


@pytest.mark.asyncio
async def test_download_attachment_bytes_returns_bytes():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"PDFDATA")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.download_attachment_bytes("ATT1")
    assert out == b"PDFDATA"


@pytest.mark.asyncio
async def test_download_attachment_bytes_returns_none_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.download_attachment_bytes("MISSING")
    assert out is None


@pytest.mark.asyncio
async def test_download_attachment_bytes_falls_back_to_cloud_when_local_empty():
    """Live-discovered (2026-05-16) bug: local Zotero desktop returns
    HTTP 200 + ``Content-Length: 0`` for group-library attachments whose
    bytes only live in Zotero cloud storage. The client must fall back
    to the cloud REST API in that case."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        if "localhost" in str(request.url) or "127.0.0.1" in str(request.url):
            # Local Zotero desktop returns 200 + empty body for group attachments
            return httpx.Response(200, content=b"")
        # Cloud REST returns the bytes
        return httpx.Response(200, content=b"%PDF-FROM-CLOUD")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(
            api_key="real-key",
            library_id="6555390",
            library_type="group",
            base_url="http://localhost:23119/api",
            http_client=http,
        )
        out = await c.download_attachment_bytes("ATT1")
    assert out == b"%PDF-FROM-CLOUD"
    # Local was tried first, cloud was the fallback
    assert any("localhost" in u for u in seen_urls)
    assert any("api.zotero.org" in u for u in seen_urls)


@pytest.mark.asyncio
async def test_download_attachment_bytes_no_cloud_fallback_when_not_local():
    """When the configured base_url is already the cloud REST API, an
    empty response should NOT trigger another cloud round-trip — it just
    means the attachment is missing or unsynced."""
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, content=b"")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        out = await c.download_attachment_bytes("ATT1")
    assert out is None
    assert len(seen_urls) == 1


@pytest.mark.asyncio
async def test_get_item_notes_strips_html():
    children = [
        {"key": "N1", "data": {"itemType": "note", "note": "<p>Hello <b>world</b></p>"}},
        {"key": "A1", "data": {"itemType": "attachment"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=children)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        notes = await c.get_item_notes("PARENT")
    assert notes == ["Hello world"]


@pytest.mark.asyncio
async def test_get_item_attachments_filters_to_attachments_only():
    children = [
        {"key": "A1", "data": {"itemType": "attachment", "contentType": "application/pdf"}},
        {"key": "N1", "data": {"itemType": "note"}},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=children)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(api_key="k", library_id="42", http_client=http)
        atts = await c.get_item_attachments("PARENT")
    assert [a["key"] for a in atts] == ["A1"]
