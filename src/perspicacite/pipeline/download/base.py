"""Base classes and utilities for download modules."""

from dataclasses import dataclass, field
from typing import Any

import httpx

from perspicacite.logging import get_logger

logger = get_logger("perspicacite.pipeline.download")


@dataclass
class DownloadResult:
    """Result of a PDF download attempt."""

    success: bool
    content: bytes | None
    source: str  # e.g., "unpaywall", "wiley", "alternative"
    error: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class ContentResult:
    """Result of a content download attempt (text/XML)."""

    success: bool
    content: str | None
    content_type: str  # "pdf", "text", "xml"
    source: str
    error: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class PaperDiscovery:
    """Result of DOI source discovery via OpenAlex + Unpaywall."""

    doi: str
    pmcid: str | None = None
    arxiv_id: str | None = None
    oa_url: str | None = None
    abstract: str | None = None
    title: str | None = None
    authors: list[str] | None = None
    year: int | None = None
    is_oa: bool = False
    work_type: str | None = None  # "article", "preprint", etc.
    unpaywall_pdf_url: str | None = None
    journal: str | None = None


@dataclass
class PaperContent:
    """Unified result from retrieve_paper_content().

    content_type values:
      - "structured": full text with sections + references (JATS XML, HTML)
      - "full_text": full text from PDF extraction (no structure)
      - "abstract": abstract only (no full text available)
      - "none": no content found

    attempts: ordered list of pipeline-step diagnostics, one per source
        actually tried. Each entry has at minimum a ``source`` label and
        a ``status`` ("miss" | "error" | "skip" | "hit"). Errors carry an
        ``error`` field. The caller can surface this in failure messages
        so an operator can tell whether the failure was config (API key
        missing) or content (genuinely not available).
    """

    success: bool
    doi: str
    content_type: str  # "structured" | "full_text" | "abstract" | "none"
    full_text: str | None = None
    sections: dict[str, str] | None = None
    references: list[dict] | None = None
    abstract: str | None = None
    content_source: str = "none"  # "pmc", "arxiv_html", "publisher_pdf", etc.
    metadata: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def record_attempt(
        self, source: str, status: str, *, error: str | None = None, **extra: Any,
    ) -> None:
        entry: dict[str, Any] = {"source": source, "status": status}
        if error:
            entry["error"] = error
        if extra:
            entry.update(extra)
        self.attempts.append(entry)


class PDFDownloader:
    """Generic PDF downloader with retry logic.

    Optional **cookie jar**: when ``cookies_path`` is set, the
    Netscape-format ``cookies.txt`` (exported from a browser logged into
    a library proxy / publisher) is attached to outgoing requests whose
    host matches ``cookie_domains``. This is the server-side equivalent
    of how the Zotero Connector browser extension grabs paywalled
    PDFs — the user does the actual SSO/proxy login in their browser
    and re-exports the cookie jar; Perspicacité just replays it.
    """

    def __init__(
        self,
        timeout: float = 30.0,
        max_retries: int = 3,
        *,
        cookies_path: str | None = None,
        cookie_domains: list[str] | None = None,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.cookies_path = cookies_path
        self.cookie_domains = list(cookie_domains or [])

    def _matches_cookie_domains(self, url: str) -> bool:
        """True when this URL's host matches the configured allowlist
        (or the allowlist is empty, meaning attach to everything)."""
        if not self.cookie_domains:
            return True
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        return any(d.lower() in host for d in self.cookie_domains)

    def _load_cookie_jar(self) -> Any:
        """Load Netscape-format cookies.txt. Returns an http.cookiejar
        compatible jar or None on failure / missing file.

        Also runs a freshness check against ``cookie_domains`` and logs
        a warning per stale domain — the most common cause of paywalled
        PDFs silently returning HTML is an expired institutional cookie.
        """
        if not self.cookies_path:
            return None
        try:
            from http.cookiejar import MozillaCookieJar
            from pathlib import Path

            from perspicacite.pipeline.download.cookies import (
                check_cookie_freshness_for_domains,
            )
            p = Path(self.cookies_path).expanduser()
            if not p.exists():
                logger.warning("pdf_cookies_path_missing", path=str(p))
                return None
            jar = MozillaCookieJar(str(p))
            jar.load(ignore_discard=True, ignore_expires=True)
            logger.info("pdf_cookies_loaded", path=str(p), count=len(jar))
            # Surface stale-domain warnings once at load time. We only
            # warn for domains that look broken — quiet on healthy ones.
            warnings = check_cookie_freshness_for_domains(
                jar, self.cookie_domains
            )
            for w in warnings:
                if w.status == "ok":
                    continue
                logger.warning(
                    "pdf_cookies_stale",
                    domain=w.domain,
                    status=w.status,
                    matched_hosts=w.matched_hosts,
                    advice=w.advice,
                )
            return jar
        except Exception as e:
            logger.warning("pdf_cookies_load_failed", error=str(e))
            return None

    async def download(
        self,
        url: str,
        http_client: httpx.AsyncClient | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes | None:
        """Download PDF from URL."""
        # Build a client. When the caller supplied one, respect it
        # (cookies from this jar can be patched into the request);
        # otherwise build one carrying the configured cookie jar.
        cookie_jar = None
        if http_client is None and self.cookies_path:
            cookie_jar = self._load_cookie_jar()
        if http_client is None:
            client_kwargs: dict[str, Any] = {
                "timeout": self.timeout,
                "follow_redirects": True,
            }
            if cookie_jar is not None and self._matches_cookie_domains(url):
                client_kwargs["cookies"] = cookie_jar
            client = httpx.AsyncClient(**client_kwargs)
        else:
            client = http_client
        should_close = http_client is None
        # Browser-like UA prevents NCBI PMC / Europe PMC from serving
        # HTML landing pages instead of actual PDFs.
        merged = {
            "User-Agent": "Mozilla/5.0 (compatible; Perspicacite/2.0)",
            **(headers or {}),
        }

        try:
            logger.info("pdf_download_start", url=url)

            response = await client.get(url, headers=merged, follow_redirects=True)
            response.raise_for_status()

            # Check if content is PDF
            content_type = response.headers.get("content-type", "").lower()
            if "pdf" not in content_type and not url.lower().endswith(".pdf"):
                if not response.content.startswith(b"%PDF"):
                    # If we're hitting a domain we have cookies configured
                    # for and got HTML back, the cookie has very likely
                    # expired. Emit a distinct log so the user sees the
                    # right fix ("re-import cookies") instead of just
                    # "PDF not found".
                    from perspicacite.pipeline.download.cookies import (
                        looks_like_paywall_html,
                    )
                    paywalled = (
                        self.cookies_path
                        and self.cookie_domains
                        and self._matches_cookie_domains(url)
                        and looks_like_paywall_html(response.content)
                    )
                    if paywalled:
                        logger.warning(
                            "pdf_cookie_likely_expired",
                            url=url,
                            content_type=content_type,
                            advice=(
                                "Publisher returned HTML on a cookie-gated "
                                "domain. Re-run `perspicacite "
                                "import-browser-cookies` to refresh."
                            ),
                        )
                    else:
                        logger.warning(
                            "pdf_download_not_pdf",
                            url=url,
                            content_type=content_type,
                        )
                    return None

            pdf_bytes = response.content

            logger.info(
                "pdf_download_success",
                url=url,
                size_bytes=len(pdf_bytes),
            )

            return pdf_bytes

        except httpx.HTTPStatusError as e:
            logger.error(
                "pdf_download_http_error",
                url=url,
                status=e.response.status_code,
            )
            return None
        except Exception as e:
            logger.error("pdf_download_error", url=url, error=str(e))
            return None
        finally:
            if should_close:
                await client.aclose()
