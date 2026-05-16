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


class ZoteroAPIError(Exception):
    """Raised when a Zotero API call fails with an unexpected status."""


class ZoteroWriteUnsupportedError(ZoteroAPIError):
    """Raised when trying to write to the local read-only API."""


def _extract_doi_from_extra(extra: str) -> str:
    """Some Zotero item types store DOIs in the free-form ``extra`` field
    as ``DOI: 10.xxxx/yyy`` lines. Pull it out for dedup matching."""
    import re
    m = re.search(r"^\s*DOI:\s*(\S+)", extra, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def _normalize_doi(doi: str | None) -> str:
    """Lowercase + strip surrounding whitespace + strip the doi.org URL prefix
    and any trailing punctuation that bibtex/landing pages sometimes leave on."""
    if not doi:
        return ""
    s = str(doi).strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.rstrip(".,;")


def _doi_matches(zotero_item: dict, normalized_doi: str) -> bool:
    """Return True when the Zotero item's DOI (in ``data.DOI`` or in the
    ``extra`` field) matches the supplied normalized DOI."""
    if not normalized_doi:
        return False
    data = zotero_item.get("data") or {}
    raw = data.get("DOI") or _extract_doi_from_extra(data.get("extra") or "")
    return _normalize_doi(raw) == normalized_doi


def _normalize_url(url: str | None) -> str:
    """Lowercase + strip scheme + strip trailing slash for URL-based dedup."""
    if not url:
        return ""
    s = str(url).strip().lower()
    for prefix in ("https://", "http://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.rstrip("/")


def _url_matches(zotero_item: dict, normalized_url: str) -> bool:
    if not normalized_url:
        return False
    data = zotero_item.get("data") or {}
    return _normalize_url(data.get("url") or "") == normalized_url


# Maps internal "kind" fields onto Zotero schema fields by itemType.
# Only the fields the create flow actually fills in are listed; everything
# else stays at Zotero's defaults.
def _build_item_body(
    *,
    item_type: str,
    paper: dict,
    doi: str,
    url: str,
    creators: list[dict],
    collection_key: str,
) -> dict:
    base = {
        "itemType": item_type,
        "title": paper.get("title") or "",
        "creators": creators,
        "abstractNote": paper.get("abstract") or "",
        "date": str(paper.get("year") or paper.get("date") or ""),
        "tags": paper.get("tags") or [],
        **({"collections": [collection_key]} if collection_key else {}),
    }
    if item_type == "journalArticle":
        base["DOI"] = doi
        base["publicationTitle"] = paper.get("journal") or paper.get("publication_title") or ""
        base["url"] = url
    elif item_type == "preprint":
        base["DOI"] = doi
        base["repository"] = paper.get("repository") or ""
        base["archiveID"] = paper.get("archive_id") or paper.get("archiveID") or ""
        base["url"] = url
    elif item_type == "webpage":
        base["url"] = url
        base["websiteTitle"] = paper.get("website_title") or paper.get("repository") or ""
    elif item_type == "computerProgram":
        base["url"] = url
        base["programmingLanguage"] = paper.get("programming_language") or ""
        base["versionNumber"] = paper.get("version") or ""
    else:
        base["DOI"] = doi
        base["url"] = url
    return base


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

    def _write_base(self) -> str:
        """Base URL for write operations (create_item, upload_attachment).

        Always cloud, even when ``base_url`` is the local desktop API —
        the local API is read-only at the Zotero level, and group writes
        always need the cloud regardless. Requires ``api_key``."""
        write_root = ZOTERO_API if self.is_local else self.base_url
        return f"{write_root}/{self.library_type}/{self.library_id}"

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

    @property
    def is_local(self) -> bool:
        """True if base_url points at the Zotero desktop local API (loopback)."""
        return "localhost" in self.base_url or "127.0.0.1" in self.base_url

    async def _find_existing_by_url(self, url: str) -> str | None:
        """Find an existing item whose ``data.url`` matches the given URL.

        Same two-stage approach as DOI dedup: search index first, recent-items
        fallback second. URL is matched after stripping the scheme and any
        trailing slash so https://example.com/x and http://example.com/x/
        compare equal.

        Uses :meth:`_write_base` so dedup hits the same library that
        :meth:`create_item` writes to (cloud, even when configured for local).
        """
        norm = _normalize_url(url)
        if not norm:
            return None
        c = await self._client()
        base = self._write_base()
        try:
            r = await c.get(
                f"{base}/items",
                params={"q": url, "qmode": "everything", "format": "json"},
                headers=self._headers(),
            )
            if r.status_code == 200:
                for item in r.json() or []:
                    if _url_matches(item, norm):
                        return item.get("key")
        except httpx.HTTPError:
            pass
        try:
            r2 = await c.get(
                f"{base}/items",
                params={"direction": "desc", "limit": 100, "format": "json"},
                headers=self._headers(),
            )
            if r2.status_code == 200:
                for item in r2.json() or []:
                    if _url_matches(item, norm):
                        return item.get("key")
        except httpx.HTTPError:
            pass
        return None

    async def _find_existing_by_doi(self, doi: str) -> str | None:
        """Find an existing item by DOI, immune to Zotero search indexing lag.

        Two-stage lookup:
        1. ``q=<doi>&qmode=everything`` — fast path, hits the search index
           (indexed within minutes-to-hours of item creation).
        2. ``direction=desc&limit=100`` — fallback for items not yet indexed;
           list the 100 most-recently-modified items and filter client-side.

        Returns the existing key if a match is found, else None.
        """
        norm = _normalize_doi(doi)
        if not norm:
            return None

        c = await self._client()
        base = self._write_base()

        # Stage 1: indexed search
        try:
            r = await c.get(
                f"{base}/items",
                params={"q": doi, "qmode": "everything", "format": "json"},
                headers=self._headers(),
            )
            if r.status_code == 200:
                for item in r.json() or []:
                    if _doi_matches(item, norm):
                        return item.get("key")
        except httpx.HTTPError:
            pass  # fall through to recent-items scan

        # Stage 2: recent-items fallback (indexing lag).
        # Even with the search index out-of-sync, the items list itself is
        # immediately consistent — checking the 100 newest items catches
        # the typical "pushed-it-myself a moment ago" double-create case.
        try:
            r2 = await c.get(
                f"{base}/items",
                params={"direction": "desc", "limit": 100, "format": "json"},
                headers=self._headers(),
            )
            if r2.status_code == 200:
                for item in r2.json() or []:
                    if _doi_matches(item, norm):
                        return item.get("key")
        except httpx.HTTPError:
            pass

        return None

    async def create_item(self, paper: dict[str, Any]) -> str | None:
        """Create a journalArticle item; returns the new (or pre-existing) key.

        Searches the library by DOI before creating to avoid duplicates.

        Writes always go to the cloud API (api.zotero.org), even when the
        client is configured with a local desktop ``base_url`` — the local
        API is read-only at the Zotero level. The required ``api_key``
        is enforced in ``__init__`` for non-loopback URLs; we re-enforce
        it here for the local-with-cloud-fallback case.

        Raises:
            ZoteroWriteUnsupportedError: when ``api_key`` is missing
                (i.e. local-only configuration with no cloud credentials).
            ZoteroAPIError: when the create POST returns an unexpected status.
        """
        if self.is_local and not self.api_key:
            raise ZoteroWriteUnsupportedError(
                "Zotero push requires the cloud API key. Set "
                "zotero.api_key in config.yml to enable push to "
                "api.zotero.org while keeping the local read base_url."
            )

        c = await self._client()
        doi = paper.get("doi")
        url = paper.get("url")
        if doi:
            existing_key = await self._find_existing_by_doi(doi)
            if existing_key:
                return existing_key
        elif url:
            existing_key = await self._find_existing_by_url(url)
            if existing_key:
                return existing_key

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

        # Item-type selection:
        # - explicit `item_type` always wins
        # - DOI present → journalArticle (or preprint for 10.48550/arXiv.*)
        # - URL only → webpage
        item_type = paper.get("item_type")
        if not item_type:
            if doi:
                item_type = (
                    "preprint"
                    if doi.lower().startswith("10.48550/arxiv.")
                    else "journalArticle"
                )
            elif url:
                item_type = "webpage"
            else:
                item_type = "journalArticle"

        body = [_build_item_body(
            item_type=item_type,
            paper=paper,
            doi=doi or "",
            url=url or "",
            creators=creators,
            collection_key=self.collection_key,
        )]
        try:
            r = await c.post(f"{self._write_base()}/items", json=body, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ZoteroAPIError(f"Zotero POST failed: {exc}") from exc
        if r.status_code not in (200, 201):
            # Surface the real error so callers can show why the push failed
            # (auth error, write-not-supported, malformed body, etc.) instead
            # of just "no key returned".
            raise ZoteroAPIError(
                f"Zotero POST /items returned {r.status_code}: {r.text[:300]}"
            )
        data = r.json() or {}
        successful = data.get("successful") or {}
        if successful:
            v = next(iter(successful.values()))
            return v.get("key") if isinstance(v, dict) else None
        success = data.get("success") or {}
        if success:
            return next(iter(success.values()))
        # Zotero accepted the request but didn't actually create — usually
        # a soft "failed" entry in the response.
        failed = data.get("failed") or {}
        if failed:
            reason = next(iter(failed.values()))
            raise ZoteroAPIError(f"Zotero create_item failed: {reason}")
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

    async def _get_attachments_via_write_base(
        self, item_key: str
    ) -> list[dict[str, Any]]:
        """Same as get_item_attachments but routed through _write_base().

        Used by :meth:`upload_attachment` for its md5-dedup pre-check —
        the upload protocol writes to cloud, so the children-list check
        must also hit cloud, otherwise local-configured clients would
        see a stale (or empty, for unsynced groups) attachment set."""
        c = await self._client()
        r = await c.get(
            f"{self._write_base()}/items/{item_key}/children",
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
        """Return raw file bytes for an attachment. None on 404/error/empty.

        Zotero's ``/items/<key>/file`` endpoint returns a 302 to S3 (for
        cloud-hosted attachments) or 200 with the bytes inline (for the
        local desktop API). ``follow_redirects=True`` is required for
        the cloud path — without it, we'd get the 302 with an empty
        body and silently return None.

        Local-API fallback: Zotero desktop's local API returns 200 with
        ``Content-Length: 0`` for group-library attachments whose bytes
        live only in Zotero cloud storage (it serves user-library files
        via ``file://`` redirect, which httpx won't follow either). When
        a local call yields empty content, retry against the cloud REST
        API if we have an api_key.
        """
        c = await self._client()
        try:
            r = await c.get(
                f"{self._base()}/items/{attachment_key}/file",
                headers=self._headers(),
                follow_redirects=True,
            )
        except httpx.HTTPError:
            r = None
        if r is not None and r.status_code == 200 and r.content:
            return r.content
        if self.is_local and self.api_key:
            return await self._download_attachment_bytes_via_cloud(attachment_key)
        return None

    async def _download_attachment_bytes_via_cloud(
        self, attachment_key: str
    ) -> bytes | None:
        """Cloud-REST fallback for ``download_attachment_bytes``.

        Builds a cloud URL from ``library_type``/``library_id`` and follows
        the S3 redirect. Used when the configured base_url is the local
        desktop API but the bytes live in Zotero cloud (group libraries
        with files only on zotero.org)."""
        c = await self._client()
        url = f"{ZOTERO_API}/{self.library_type}/{self.library_id}/items/{attachment_key}/file"
        try:
            r = await c.get(url, headers=self._headers(), follow_redirects=True)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.content:
            return None
        return r.content

    async def upload_attachment(
        self,
        *,
        parent_item_key: str,
        file_path: str,
        filename: str | None = None,
        content_type: str = "application/pdf",
    ) -> str | None:
        """Upload a file as a child attachment of ``parent_item_key``.

        Implements Zotero's documented 3-step file-upload protocol
        (https://www.zotero.org/support/dev/web_api/v3/file_upload):

        1. POST `/items` with ``itemType=attachment, linkMode=imported_file,
           parentItem, filename, contentType, md5, mtime, filesize``
           to register the attachment metadata; receive its item key.
        2. POST `/items/<key>/file` (form-encoded, with
           ``If-None-Match: *``) to request upload credentials
           (returns ``{url, params, uploadKey, ...}``; or ``{exists: 1}``
           when the same content already exists server-side).
        3. PUT the bytes to ``url`` with the returned ``params`` as a
           multipart body.
        4. POST `/items/<key>/file` again with ``upload=<uploadKey>``
           and ``If-None-Match: *`` to register the upload as complete.

        Cloud-only — the local desktop API doesn't support attachment
        upload through this protocol; group writes also require the
        cloud API. Returns the attachment item key on success; ``None``
        on hard failure (logged via :exc:`ZoteroAPIError`).
        """
        import hashlib
        import os

        if self.is_local and not self.api_key:
            raise ZoteroWriteUnsupportedError(
                "Zotero attachment upload requires the cloud API key. "
                "Set zotero.api_key in config.yml to enable upload to "
                "api.zotero.org while keeping the local read base_url."
            )
        from pathlib import Path
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise ZoteroAPIError(f"upload_attachment: file not found: {path}")
        data = path.read_bytes()
        if not data:
            raise ZoteroAPIError(f"upload_attachment: empty file: {path}")
        fname = filename or path.name
        md5 = hashlib.md5(data).hexdigest()
        mtime_ms = int(path.stat().st_mtime * 1000)

        c = await self._client()

        # Pre-check: skip the 3-step protocol entirely if the parent
        # already has an attachment with the same md5. Otherwise step 2
        # rejects the upload with HTTP 412 "If-None-Match: * set but
        # file exists" — discovered live 2026-05-16 when retrying
        # push_to_zotero(attach_pdf=True) on the same DOI.
        try:
            existing = await self._get_attachments_via_write_base(parent_item_key)
            for att in existing:
                att_data = att.get("data") or {}
                if att_data.get("md5") == md5:
                    return att.get("key")
        except httpx.HTTPError:
            # Children-list lookup is best-effort; on failure fall through
            # and let the upload protocol attempt run as before.
            pass

        # Step 1 — register the attachment shell.
        register_body = [{
            "itemType": "attachment",
            "parentItem": parent_item_key,
            "linkMode": "imported_file",
            "title": fname,
            "filename": fname,
            "contentType": content_type,
            "md5": md5,
            "mtime": mtime_ms,
        }]
        r = await c.post(
            f"{self._write_base()}/items",
            json=register_body,
            headers=self._headers(),
        )
        if r.status_code not in (200, 201):
            raise ZoteroAPIError(
                f"Zotero attach step1 (register) returned {r.status_code}: "
                f"{r.text[:300]}"
            )
        body = r.json() or {}
        successful = body.get("successful") or {}
        if not successful:
            failed = body.get("failed") or {}
            raise ZoteroAPIError(
                f"Zotero attach step1 (register) failed: {failed or body}"
            )
        attach_key = next(iter(successful.values())).get("key")
        if not attach_key:
            raise ZoteroAPIError("Zotero attach step1: no key returned")

        # Step 2 — request upload credentials. Form-encoded body, NOT JSON.
        # If-None-Match: * — required for a fresh attachment slot.
        cred_headers = {
            "Zotero-API-Key": self.api_key,
            "Zotero-API-Version": "3",
            "Content-Type": "application/x-www-form-urlencoded",
            "If-None-Match": "*",
        }
        cred_form = {
            "md5": md5,
            "filename": fname,
            "filesize": str(len(data)),
            "mtime": str(mtime_ms),
        }
        r2 = await c.post(
            f"{self._write_base()}/items/{attach_key}/file",
            data=cred_form,
            headers=cred_headers,
        )
        if r2.status_code != 200:
            raise ZoteroAPIError(
                f"Zotero attach step2 (creds) returned {r2.status_code}: "
                f"{r2.text[:300]}"
            )
        creds = r2.json() or {}
        # Server-side dedup: identical content already uploaded; we're done.
        if creds.get("exists"):
            return attach_key

        upload_url = creds.get("url")
        upload_params = creds.get("params") or {}
        upload_key = creds.get("uploadKey")
        if not upload_url or not upload_key:
            raise ZoteroAPIError(
                f"Zotero attach step2: bad creds payload {list(creds)}"
            )

        # Step 3 — POST the bytes to the storage URL. Zotero's docs use
        # multipart/form-data with the actual file under ``file``.
        # Use a separate (un-authenticated) request — the upload URL is
        # presigned.
        files = {"file": (fname, data, content_type)}
        # ``params`` from Zotero are form fields that must accompany the
        # bytes (e.g. ``key``, ``acl``, ``policy``, etc. for the S3-like
        # store). httpx multipart: pass them as ``data``.
        r3 = await c.post(
            upload_url,
            data=upload_params,
            files=files,
        )
        if r3.status_code not in (200, 201, 204):
            raise ZoteroAPIError(
                f"Zotero attach step3 (storage POST) returned "
                f"{r3.status_code}: {r3.text[:300]}"
            )

        # Step 4 — finalize.
        finalize_form = {"upload": upload_key}
        r4 = await c.post(
            f"{self._write_base()}/items/{attach_key}/file",
            data=finalize_form,
            headers=cred_headers,
        )
        if r4.status_code not in (200, 204):
            raise ZoteroAPIError(
                f"Zotero attach step4 (finalize) returned {r4.status_code}: "
                f"{r4.text[:300]}"
            )
        return attach_key

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
