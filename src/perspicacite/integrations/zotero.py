"""Zotero Web API v3 client — create journalArticle items with DOI dedup."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

ZOTERO_API = "https://api.zotero.org"


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        return " ".join("".join(self._chunks).split()).strip()


def _html_to_text(html: str) -> str:
    """Strip HTML tags; collapse whitespace."""
    p = _HTMLStripper()
    p.feed(html or "")
    return p.get_text()


class ZoteroClient:
    def __init__(
        self,
        *,
        api_key: str,
        library_id: str,
        library_type: str = "user",
        collection_key: str = "",
        http_client: httpx.AsyncClient | None = None,
    ):
        if not api_key or not library_id:
            raise ValueError("Zotero api_key and library_id are required")
        self.api_key = api_key
        self.library_id = library_id
        self.library_type = "groups" if library_type == "group" else "users"
        self.collection_key = collection_key
        self._http = http_client

    def _base(self) -> str:
        return f"{ZOTERO_API}/{self.library_type}/{self.library_id}"

    def _headers(self) -> dict[str, str]:
        return {
            "Zotero-API-Key": self.api_key,
            "Zotero-API-Version": "3",
            "Content-Type": "application/json",
        }

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def create_item(self, paper: dict[str, Any]) -> str | None:
        """Create a journalArticle item; returns the new (or pre-existing) key.

        Searches the library by DOI before creating to avoid duplicates.
        Returns None if creation fails.
        """
        c = await self._client()
        doi = paper.get("doi")
        if doi:
            try:
                r = await c.get(
                    f"{self._base()}/items",
                    params={"q": doi, "qmode": "everything", "format": "json"},
                    headers=self._headers(),
                )
                if r.status_code == 200:
                    for item in r.json() or []:
                        existing_doi = (item.get("data") or {}).get("DOI") or ""
                        if existing_doi.lower() == doi.lower():
                            return item.get("key")
            except httpx.HTTPError:
                pass  # fall through to create

        creators = []
        for a in (paper.get("authors") or []):
            # Handle "First Last" → split on first space; fall back to lastName-only
            parts = str(a).split(" ", 1)
            if len(parts) == 2:
                creators.append({"creatorType": "author", "firstName": parts[0], "lastName": parts[1]})
            else:
                creators.append({"creatorType": "author", "firstName": "", "lastName": str(a)})
        if not creators:
            creators = [{"creatorType": "author", "firstName": "", "lastName": "Unknown"}]

        body = [{
            "itemType": "journalArticle",
            "title": paper.get("title") or "",
            "DOI": doi or "",
            "date": str(paper.get("year") or ""),
            "publicationTitle": paper.get("journal") or "",
            "abstractNote": paper.get("abstract") or "",
            "creators": creators,
            **({"collections": [self.collection_key]} if self.collection_key else {}),
        }]
        try:
            r = await c.post(f"{self._base()}/items", json=body, headers=self._headers())
        except httpx.HTTPError:
            return None
        if r.status_code not in (200, 201):
            return None
        data = r.json() or {}
        successful = data.get("successful") or {}
        if successful:
            v = next(iter(successful.values()))
            return v.get("key") if isinstance(v, dict) else None
        success = data.get("success") or {}
        if success:
            return next(iter(success.values()))
        return None

    async def _paginated(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        c = await self._client()
        out: list[dict[str, Any]] = []
        start = 0
        limit = 100
        while True:
            p: dict[str, Any] = {"start": start, "limit": limit, "format": "json"}
            if params:
                p.update(params)
            r = await c.get(f"{self._base()}{path}", params=p, headers=self._headers())
            if r.status_code != 200:
                break
            page = r.json() or []
            if not page:
                break
            out.extend(page)
            start += len(page)
        return out

    async def list_collections(self) -> list[dict[str, Any]]:
        """All collections (paginated)."""
        return await self._paginated("/collections")

    async def list_top_level_collections(self) -> list[dict[str, Any]]:
        """Top-level collections only (no parent)."""
        return await self._paginated("/collections/top")

    async def list_items_in_collection(
        self, coll_key: str, *, include_subcollections: bool = True
    ) -> list[dict[str, Any]]:
        """Items in a collection. Excludes attachments and notes (those come via
        get_item_attachments / get_item_notes per parent item).
        When include_subcollections=True, rolls up items from descendant collections."""
        items = await self._paginated(
            f"/collections/{coll_key}/items",
            params={"itemType": "-attachment || note"},
        )
        if include_subcollections:
            all_colls = await self.list_collections()
            descendants = [
                c["key"] for c in all_colls
                if (c.get("data") or {}).get("parentCollection") == coll_key
            ]
            for d in descendants:
                items.extend(await self.list_items_in_collection(d, include_subcollections=True))
        seen: set[str] = set()
        uniq: list[dict[str, Any]] = []
        for it in items:
            k = it.get("key")
            if k and k not in seen:
                seen.add(k)
                uniq.append(it)
        return uniq

    async def list_top_level_items_without_collection(self) -> list[dict[str, Any]]:
        """Top-level library items not in any collection."""
        items = await self._paginated(
            "/items/top",
            params={"itemType": "-attachment || note"},
        )
        return [it for it in items if not ((it.get("data") or {}).get("collections") or [])]

    async def get_item_attachments(self, item_key: str) -> list[dict[str, Any]]:
        """Children of item_key where itemType == 'attachment'."""
        c = await self._client()
        r = await c.get(
            f"{self._base()}/items/{item_key}/children",
            params={"format": "json"},
            headers=self._headers(),
        )
        if r.status_code != 200:
            return []
        return [
            it for it in (r.json() or [])
            if ((it.get("data") or {}).get("itemType")) == "attachment"
        ]

    async def download_attachment_bytes(self, attachment_key: str) -> bytes | None:
        """Return raw file bytes for an attachment. None on 404/error/empty."""
        c = await self._client()
        try:
            r = await c.get(
                f"{self._base()}/items/{attachment_key}/file",
                headers=self._headers(),
            )
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.content:
            return None
        return r.content

    async def get_item_notes(self, item_key: str) -> list[str]:
        """Plain-text content of all 'note' children of item_key (HTML stripped)."""
        c = await self._client()
        r = await c.get(
            f"{self._base()}/items/{item_key}/children",
            params={"format": "json"},
            headers=self._headers(),
        )
        if r.status_code != 200:
            return []
        out: list[str] = []
        for it in r.json() or []:
            data = it.get("data") or {}
            if data.get("itemType") == "note":
                out.append(_html_to_text(data.get("note") or ""))
        return out
