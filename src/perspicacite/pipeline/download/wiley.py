"""Wiley TDM (Text and Data Mining) API.

Official pattern (Wiley developer / TDM docs and examples):
``GET https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}`` with header
``Wiley-TDM-Client-Token: <token>``.

Overview: https://onlinelibrary.wiley.com/library-info/resources/text-and-datamining
Developer hub: https://developer.wiley.com/api/wiley-text-and-data-mining-tdm/

Wiley documents rate limits (commonly ~3 requests per second); callers should
throttle bulk downloads. DOIs with reserved characters should be URL-encoded
in the path per Wiley guidance.
"""

import os

import httpx

from perspicacite.logging import get_logger
from .base import logger


async def download_from_wiley_tdm(
    doi: str,
    api_token: str,
    http_client: httpx.AsyncClient | None = None,
) -> bytes | None:
    """
    Download PDF from Wiley TDM (Text and Data Mining) API.

    Args:
        doi: DOI to download
        api_token: Wiley TDM API client token
        http_client: Optional HTTP client

    Returns:
        PDF bytes or None if download failed
    """
    client = http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    should_close = http_client is None

    try:
        # Clean DOI - remove prefix if present
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")

        url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{clean_doi}"

        logger.info("wiley_tdm_attempt", doi=doi, url=url)

        headers = {
            "Wiley-TDM-Client-Token": api_token,
            "User-Agent": "Perspicacite/2.0",
        }

        response = await client.get(url, headers=headers)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()

        # Check if we got a PDF
        if "pdf" in content_type or response.content.startswith(b"%PDF"):
            logger.info("wiley_tdm_success", doi=doi, size_bytes=len(response.content))
            return response.content
        else:
            logger.warning("wiley_tdm_not_pdf", doi=doi, content_type=content_type)
            return None

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 403:
            logger.warning("wiley_tdm_not_entitled", doi=doi)
        else:
            logger.error(
                "wiley_tdm_http_error",
                doi=doi,
                status=e.response.status_code,
            )
        return None
    except Exception as e:
        logger.error("wiley_tdm_error", doi=doi, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()


async def download_from_wiley_direct(
    doi: str,
    http_client: httpx.AsyncClient | None = None,
) -> bytes | None:
    """Try publisher PDF URL without TDM token (open access or institutional IP).

    This is not the Wiley TDM API; it uses the same ``/doi/pdf/`` pattern as a
    browser. Use after Unpaywall/OpenAlex when no TDM token is configured.
    """
    client = http_client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    should_close = http_client is None

    try:
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "")
        # /doi/pdfdirect/<doi> is the raw-PDF endpoint the browser's
        # "Download PDF" button uses. /doi/pdf/<doi> returns the ePDF
        # reader HTML wrapper instead of bytes — useless for ingestion.
        url = f"https://onlinelibrary.wiley.com/doi/pdfdirect/{clean_doi}"
        logger.info("wiley_direct_attempt", doi=clean_doi, url=url)
        # Wiley sits behind Cloudflare; cf_clearance cookies (issued to the
        # user's browser) are tied to the User-Agent that obtained them.
        # Sending a polite-bot UA invalidates the clearance and we get 403,
        # even with valid institutional cookies. Use a current Chrome UA so
        # the cookies we replay still match the browser's WAF challenge.
        cookies_attached = bool(getattr(client, "cookies", None))
        # Wiley sits behind Cloudflare; cf_clearance cookies (issued to
        # the user's browser) are tied to the User-Agent that obtained
        # them, AND the WAF checks Referer chain on /doi/pdf hits.
        # When cookies are attached, mimic a real browser click from
        # the article landing page; otherwise stay polite-bot.
        if cookies_attached:
            ua = (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            )
            headers = {
                "User-Agent": ua,
                "Referer": f"https://onlinelibrary.wiley.com/doi/{clean_doi}",
                "Accept": "application/pdf,application/x-pdf,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        else:
            mailto = os.getenv("UNPAYWALL_EMAIL") or os.getenv("OPENALEX_MAILTO")
            ua = f"Perspicacite/2.0 (mailto:{mailto})" if mailto else "Perspicacite/2.0"
            headers = {"User-Agent": ua}
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.content.startswith(b"%PDF"):
            logger.info("wiley_direct_success", doi=clean_doi, size_bytes=len(response.content))
            return response.content
        logger.warning("wiley_direct_not_pdf", doi=clean_doi, content_type=content_type)
        return None
    except httpx.HTTPStatusError as e:
        if e.response.status_code in (403, 404):
            logger.info("wiley_direct_no_access", doi=doi, status=e.response.status_code)
        else:
            logger.warning("wiley_direct_http_error", doi=doi, status=e.response.status_code)
        return None
    except Exception as e:
        logger.error("wiley_direct_error", doi=doi, error=str(e))
        return None
    finally:
        if should_close:
            await client.aclose()
