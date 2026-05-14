"""Zotero Web API v3 client — create journalArticle items with DOI dedup."""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import httpx

ZOTERO_API = "https://api.zotero.org"
"""Default base URL for the cloud Zotero Web API v3.

Override per-client by passing ``base_url`` to :class:`ZoteroClient`. For the
desktop app's local API (which serves attachments from local storage,
including Linked Files), point at ``http://localhost:23119/api`` and make sure
"Allow other applications on this computer to communicate with Zotero" is
enabled (Settings → Advanced → Config Editor → ``extensions.zotero.httpServer.enabled``).
"""


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
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        # Cloud requires api_key. Local desktop API (port 23119) accepts an
        # empty api_key — it trusts loopback clients. Allow both by only
        # requiring api_key when talking to a non-loopback host.
        if not library_id:
            raise ValueError("Zotero library_id is required")
        self.base_url = (base_url or ZOTERO_API).rstrip("/")
        if not api_key and "localhost" not in self.base_url and "127.0.0.1" not in self.base_url:
            raise ValueError("Zotero api_key is required for non-local base_url")
        self.api_key = api_key
        self.library_id = library_id
        self.library_type = "groups" if library_type == "group" else "users"
        self.collection_key = collection_key
        self._http = http_client

    def _base(self) -> str:
        return f"{self.base_url}/{self.library_type}/{self.library_id}"

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
        import asyncio as _asyncio
        c = await self._client()
        out: list[dict[str, Any]] = []
        start = 0
        limit = 100
        while True:
            p: dict[str, Any] = {"start": start, "limit": limit, "format": "json"}
            if params:
                p.update(params)
            # Up to 3 retries on 429/5xx — Zotero returns a Retry-After
            # header on 429; respect it (clamped to 60s for the test
            # path). On unrecoverable error, raise so callers can surface
            # the failure instead of silently returning a short list.
            attempt = 0
            while True:
                r = await c.get(f"{self._base()}{path}", params=p, headers=self._headers())
                if r.status_code == 200:
                    break
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                    retry_after = r.headers.get("retry-after") or r.headers.get("Retry-After")
                    try:
                        wait = float(retry_after) if retry_after else 2.0 * (2 ** attempt)
                    except ValueError:
                        wait = 2.0 * (2 ** attempt)
                    wait = min(wait, 60.0)
                    await _asyncio.sleep(wait)
                    attempt += 1
                    continue
                raise httpx.HTTPStatusError(
                    f"Zotero API returned {r.status_code} for {path} "
                    f"(retry-after={r.headers.get('retry-after')})",
                    request=r.request,
                    response=r,
                )
            page = r.json() or []
            if not page:
                break
            out.extend(page)
            start += len(page)
        return out

    async def list_collections(self) -> list[dict[str, Any]]:
        """All collections (paginated)."""
        return await self._paginated("/collections")

    async def get_library_name(self) -> str | None:
        """Resolve the human-readable library name.

        For a group library: returns the group's name (e.g. "BioMedOmicsAI").
        For a user library: returns the username when available.
        Falls back to None if the API call fails — callers should use a
        default like "Library" in that case.
        """
        c = await self._client()
        if self.library_type == "groups":
            # The /groups/<id> endpoint returns the group metadata for the
            # library_id we're scoped to. On the local API this is the
            # bare group; on the cloud API it's wrapped in a list when
            # queried via /users/<userId>/groups, but a direct
            # /groups/<id> returns the bare object.
            try:
                r = await c.get(f"{self.base_url}/groups/{self.library_id}",
                                headers=self._headers())
                if r.status_code == 200:
                    body = r.json() or {}
                    data = body.get("data") or body
                    name = data.get("name")
                    if isinstance(name, str) and name:
                        return name
            except httpx.HTTPError:
                pass
            return None
        # User library — try to read /keys/current for username.
        try:
            r = await c.get(f"{self.base_url}/keys/current",
                            headers=self._headers())
            if r.status_code == 200:
                body = r.json() or {}
                username = body.get("username")
                if isinstance(username, str) and username:
                    return username
        except httpx.HTTPError:
            pass
        return None

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
