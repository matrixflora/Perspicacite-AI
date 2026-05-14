"""Supplementary Information fetchers.

Three sources, in order of reliability:

1. **PMC JATS** — handled in ``pmc.get_supplementary_from_pmc``; URLs at
   ``https://www.ncbi.nlm.nih.gov/pmc/articles/<pmcid>/bin/<file>``.
2. **Springer / Nature ESM** — article landing-page HTML contains
   ``static-content.springer.com/esm/...`` links.
3. **ACS supporting info** — ``pubs.acs.org/doi/suppl/<doi>`` listing
   page enumerates files served at
   ``pubs.acs.org/doi/suppl/<doi>/suppl_file/<filename>``.

This module wraps the URL discovery + bytes download for items 2 and 3,
and provides ``fetch_supplementary_file(url)`` so callers can pull bytes
for any SI URL (including those already in the PMC manifest).

The bytes fetcher enforces a per-file size cap by default — SI files
can be hundreds of MB (raw data, videos). Callers wanting large files
should pass ``max_bytes`` explicitly.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.download.supplementary")


# Reasonable default cap so a stray SI link to a 500 MB raw dataset
# doesn't silently exhaust disk.
DEFAULT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB


async def fetch_supplementary_file(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = 60.0,
) -> bytes | None:
    """Download a single SI file. Returns bytes or None on failure.

    Enforces ``max_bytes`` (default 50 MB). Logs the rejection reason
    on cap-overflow so callers can opt for a larger cap if needed.
    """
    if not url:
        return None
    client = http_client or httpx.AsyncClient(timeout=timeout, follow_redirects=True)
    should_close = http_client is None
    try:
        r = await client.get(url)
        if r.status_code != 200:
            logger.info("si_fetch_non_200", url=url, status=r.status_code)
            return None
        body = r.content
        if len(body) > max_bytes:
            logger.warning(
                "si_fetch_exceeds_cap",
                url=url,
                size=len(body),
                cap=max_bytes,
            )
            return None
        return body
    except (httpx.HTTPError, OSError) as e:
        logger.info("si_fetch_failed", url=url, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()


# -----------------------------------------------------------------------------
# Springer / Nature ESM
# -----------------------------------------------------------------------------


_SPRINGER_HOSTS = (
    "link.springer.com",
    "www.nature.com",
    "www.springer.com",
)

# https://static-content.springer.com/esm/art%3A10.1038%2Fs41586-022-04477-8/MediaObjects/41586_2022_4477_MOESM1_ESM.pdf
_SPRINGER_ESM_RE = re.compile(
    r"https?://static-content\.springer\.com/esm/[^\s\"'<>]+",
    re.IGNORECASE,
)


async def extract_springer_esm_urls(
    doi: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]] | None:
    """Find Springer/Nature ESM SI URLs by scraping the article landing page.

    Returns a list of ``{"url", "filename", "mime_type" (best-effort guess)}``
    or ``None`` when no Springer ESM links are found (paper isn't on a
    Springer/Nature property, or the HTML doesn't expose ESM links).
    """
    clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if not clean:
        return None

    # Article landing-page URL. nature.com uses /articles/<id>, but
    # https://doi.org/<doi> redirects to the right place.
    landing_url = f"https://doi.org/{clean}"
    client = http_client or httpx.AsyncClient(timeout=45.0, follow_redirects=True)
    should_close = http_client is None
    try:
        r = await client.get(landing_url)
        if r.status_code != 200 or not r.text:
            return None
        # Only proceed if we landed on a Springer/Nature host (DOIs from
        # other publishers will redirect elsewhere and the regex below
        # would still match nothing — but skipping early avoids work).
        final_host = (r.url.host or "").lower()
        if not any(h in final_host for h in _SPRINGER_HOSTS):
            return None
        urls = list(dict.fromkeys(_SPRINGER_ESM_RE.findall(r.text)))
        if not urls:
            return None
        out: list[dict[str, Any]] = []
        for u in urls:
            # Decode %3A etc. in the path so filenames are readable.
            decoded = urllib.parse.unquote(u)
            fname = decoded.rsplit("/", 1)[-1]
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            mime_map = {
                "pdf": "application/pdf",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xls": "application/vnd.ms-excel",
                "csv": "text/csv",
                "zip": "application/zip",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "doc": "application/msword",
                "txt": "text/plain",
                "mp4": "video/mp4",
            }
            out.append({
                "url": u,
                "filename": fname,
                "mime_type": mime_map.get(ext),
            })
        logger.info("springer_esm_found", doi=clean, count=len(out), host=final_host)
        return out
    except (httpx.HTTPError, OSError) as e:
        logger.info("springer_esm_failed", doi=clean, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()


# -----------------------------------------------------------------------------
# ACS supporting information
# -----------------------------------------------------------------------------


# Files live at pubs.acs.org/doi/suppl/<doi>/suppl_file/<filename>
_ACS_SUPPL_RE = re.compile(
    r"https?://pubs\.acs\.org/doi/suppl/[^\s\"'<>]+/suppl_file/[^\s\"'<>]+",
    re.IGNORECASE,
)


async def extract_acs_supporting_info_urls(
    doi: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]] | None:
    """Find ACS supporting-info file URLs from the per-DOI suppl page.

    ACS publishes a per-article supporting-info listing at
    ``https://pubs.acs.org/doi/suppl/<doi>``. The page lists files
    served at ``pubs.acs.org/doi/suppl/<doi>/suppl_file/<filename>``.

    Returns a list of ``{"url", "filename", "mime_type"}`` or ``None``
    when no ACS SI links are found.
    """
    clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    if not clean:
        return None

    suppl_url = f"https://pubs.acs.org/doi/suppl/{clean}"
    client = http_client or httpx.AsyncClient(timeout=45.0, follow_redirects=True)
    should_close = http_client is None
    try:
        r = await client.get(suppl_url)
        if r.status_code != 200 or not r.text:
            return None
        urls = list(dict.fromkeys(_ACS_SUPPL_RE.findall(r.text)))
        # Also rewrite suppl_file paths that appear as relative hrefs:
        # `<a href="/doi/suppl/<doi>/suppl_file/<file>">` → absolute form.
        rel_re = re.compile(r"/doi/suppl/[^\s\"'<>]+/suppl_file/[^\s\"'<>]+")
        for m in rel_re.findall(r.text):
            urls.append(f"https://pubs.acs.org{m}")
        urls = list(dict.fromkeys(urls))
        if not urls:
            return None
        out: list[dict[str, Any]] = []
        for u in urls:
            fname = u.rsplit("/", 1)[-1]
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            mime_map = {
                "pdf": "application/pdf",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "xls": "application/vnd.ms-excel",
                "csv": "text/csv",
                "zip": "application/zip",
                "txt": "text/plain",
                "cif": "chemical/x-cif",
            }
            out.append({
                "url": u,
                "filename": fname,
                "mime_type": mime_map.get(ext),
            })
        logger.info("acs_si_found", doi=clean, count=len(out))
        return out
    except (httpx.HTTPError, OSError) as e:
        logger.info("acs_si_failed", doi=clean, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()


# -----------------------------------------------------------------------------
# Unified discovery
# -----------------------------------------------------------------------------


async def discover_supplementary(
    doi: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Try all SI sources for a DOI and return a unified manifest.

    Result shape:
        {
          "items": [ {label, caption, url, mime_type, source}, … ],
          "sources_tried": ["pmc", "springer", "acs"],
        }

    Items from different sources are merged and tagged with the source
    in their ``source`` field. PMC is preferred when available (most
    structured, with labels and captions); Springer/ACS provide URL-only
    entries when PMC isn't available.
    """
    from perspicacite.pipeline.download.pmc import get_supplementary_from_pmc

    items: list[dict[str, Any]] = []
    sources_tried: list[str] = []

    # PMC — best metadata when available
    sources_tried.append("pmc")
    pmc_items = await get_supplementary_from_pmc(doi, http_client=http_client)
    if pmc_items:
        for it in pmc_items:
            items.append({**it, "source": "pmc"})

    # Springer / Nature ESM — only if no PMC hits (PMC is more reliable)
    if not items:
        sources_tried.append("springer")
        springer = await extract_springer_esm_urls(doi, http_client=http_client)
        if springer:
            for it in springer:
                items.append({"url": it["url"], "filename": it["filename"],
                              "mime_type": it.get("mime_type"), "source": "springer"})

    # ACS — only if nothing else
    if not items:
        sources_tried.append("acs")
        acs = await extract_acs_supporting_info_urls(doi, http_client=http_client)
        if acs:
            for it in acs:
                items.append({"url": it["url"], "filename": it["filename"],
                              "mime_type": it.get("mime_type"), "source": "acs"})

    return {"items": items, "sources_tried": sources_tried}


async def download_supplementary_to_capsule(
    capsule_dir: Path,
    manifest_items: list[dict[str, Any]],
    *,
    http_client: httpx.AsyncClient | None = None,
    max_bytes_per_file: int = DEFAULT_MAX_BYTES,
    max_bytes_per_record: int = 200 * 1024 * 1024,  # 200 MB
    text_only: bool = False,
) -> dict[str, Any]:
    """Download SI bytes listed in ``manifest_items`` into the capsule.

    Files go under ``<capsule>/supplementary/files/<filename>``. A summary
    is written to ``<capsule>/supplementary/fetched.json``.

    Caps:
        - ``max_bytes_per_file`` — skip individual file if it exceeds
          (default 50 MB)
        - ``max_bytes_per_record`` — stop the loop once cumulative bytes
          across this paper's SI exceed this (default 200 MB)
        - ``text_only`` — when True, skip mime types we can't easily
          chunk (zip / mp4 / archive). PDFs/XLSX/CSV/TXT are kept.

    Returns ``{"fetched": [...], "skipped": [...], "bytes": int}``.
    """
    files_dir = capsule_dir / "supplementary" / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    fetched: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    total_bytes = 0
    client = http_client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
    should_close = http_client is None

    # Bytes we won't process as text. Conservative — we keep PDFs since
    # the chunker can read them; opaque archives/video get filtered when
    # text_only=True so the chunker doesn't choke.
    NON_TEXT_MIMES = {
        "application/zip",
        "application/x-tar",
        "application/x-gzip",
        "video/mp4",
        "application/octet-stream",
    }

    try:
        for it in manifest_items:
            url = it.get("url")
            if not url:
                skipped.append({"item": it, "reason": "no_url"})
                continue
            mime = (it.get("mime_type") or "").lower()
            if text_only and mime in NON_TEXT_MIMES:
                skipped.append({"item": it, "reason": "non_text_mime", "mime_type": mime})
                continue
            if total_bytes >= max_bytes_per_record:
                skipped.append({"item": it, "reason": "record_cap_reached"})
                continue
            data = await fetch_supplementary_file(
                url, http_client=client, max_bytes=max_bytes_per_file,
            )
            if data is None:
                skipped.append({"item": it, "reason": "fetch_failed"})
                continue
            # Use filename from manifest when present; fall back to URL tail.
            fname = (
                it.get("filename")
                or it.get("href")
                or url.rsplit("/", 1)[-1]
            )
            # Sanitize: no slashes or null bytes.
            fname = fname.replace("/", "_").replace("\x00", "")
            (files_dir / fname).write_bytes(data)
            total_bytes += len(data)
            fetched.append({
                "url": url,
                "filename": fname,
                "size": len(data),
                "mime_type": it.get("mime_type"),
                "label": it.get("label"),
                "caption": it.get("caption"),
                "source": it.get("source"),
            })

        summary = {
            "fetched": fetched,
            "skipped": skipped,
            "bytes": total_bytes,
        }
        import json
        (capsule_dir / "supplementary" / "fetched.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary
    finally:
        if should_close:
            await client.aclose()
