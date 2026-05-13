"""Zotero Web API v3 client — create journalArticle items with DOI dedup."""

from __future__ import annotations

from typing import Any

import httpx

ZOTERO_API = "https://api.zotero.org"


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
                        if (item.get("data") or {}).get("DOI", "").lower() == doi.lower():
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
