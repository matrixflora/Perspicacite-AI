"""ZoteroClient read methods + HTML-to-text helper."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from perspicacite.integrations.zotero import (
    ZoteroClient,
    _file_url_to_candidate_paths,
    _html_to_text,
)


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


def test_file_url_to_candidate_paths_cross_platform():
    """The file:// -> on-disk mapping must work regardless of where the
    backend runs, not just on one machine."""
    # native Linux/macOS: POSIX path as-is
    assert _file_url_to_candidate_paths("file:///home/u/Zotero/storage/AB/x.pdf") == [
        Path("/home/u/Zotero/storage/AB/x.pdf")
    ]

    # Windows drive: native-Windows path first, then the WSL /mnt mount.
    # %20 must be decoded back to a space.
    cands = _file_url_to_candidate_paths("file:///C:/Users/Tao%20Jiang/Zotero/storage/AB/x.pdf")
    assert cands == [
        Path("C:/Users/Tao Jiang/Zotero/storage/AB/x.pdf"),
        Path("/mnt/c/Users/Tao Jiang/Zotero/storage/AB/x.pdf"),
    ]

    # Drive folded into the authority (file://C:/...) is normalized too.
    assert Path("/mnt/d/data/p.pdf") in _file_url_to_candidate_paths("file://D:/data/p.pdf")

    # An empty file:// URL yields no candidates.
    assert _file_url_to_candidate_paths("file://") == []


@pytest.mark.asyncio
async def test_download_attachment_bytes_reads_local_file_redirect(tmp_path):
    """The desktop local API 302-redirects /file to a file:// path on disk;
    the client must read that file instead of trying to HTTP-follow file://."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.6 local-bytes")
    file_uri = pdf.as_uri()  # file:///.../paper.pdf

    def handler(request: httpx.Request) -> httpx.Response:
        # Local desktop API: redirect to the on-disk file, no body.
        return httpx.Response(302, headers={"Location": file_uri})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http:
        c = ZoteroClient(
            api_key="",  # loopback needs no key
            library_id="42",
            base_url="http://localhost:23119/api",
            http_client=http,
        )
        out = await c.download_attachment_bytes("ATT1")
    assert out == b"%PDF-1.6 local-bytes"


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
