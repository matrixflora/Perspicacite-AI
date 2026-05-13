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
