"""POST /api/pdf-dropzone — accept a user-uploaded PDF for a DOI.

Companion to ``push_to_zotero(..., attach_pdf=True)`` for the paywalled
content cases where neither the cookies-aware HTTP client nor the
(future, behind-flag) headless-Chromium fallback can fetch the file
automatically.

User flow:
  1. User opens the article in their authenticated browser
     (institutional VPN, SSO, library proxy) and clicks "Download PDF"
  2. User uploads the downloaded file through this endpoint with the DOI
  3. Endpoint writes it to ``pdf_download.cache_dir`` using the same
     naming convention as :mod:`pdf_cache`
  4. Next call to ``push_to_zotero(..., attach_pdf=True)`` picks it up
     from cache — no re-fetch attempted

The endpoint is also useful for testing the rest of the attachment
pipeline without round-tripping to a live publisher every time.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from perspicacite.logging import get_logger
from perspicacite.pipeline.download.pdf_cache import (
    cached_pdf_path,
    store_pdf,
)
from perspicacite.web.state import app_state

logger = get_logger("perspicacite.web.routers.pdf_dropzone")

router = APIRouter(prefix="/api", tags=["pdf-dropzone"])


_MAX_PDF_BYTES = 200 * 1024 * 1024  # 200 MB — refuses anything larger
_PDF_MAGIC = b"%PDF"


@router.post("/pdf-dropzone")
async def pdf_dropzone(
    doi: str = Form(..., description="DOI to associate the PDF with."),
    file: UploadFile = File(..., description="The PDF binary."),
) -> JSONResponse:
    """Upload a single PDF and store it in the DOI cache."""
    cfg = getattr(app_state.config, "pdf_download", None) if app_state.config else None
    cache_dir = getattr(cfg, "cache_dir", None) if cfg else None
    if not cache_dir:
        raise HTTPException(
            status_code=400,
            detail="pdf_download.cache_dir is not configured",
        )

    if not doi or not doi.strip():
        raise HTTPException(status_code=400, detail="doi is required")
    doi_clean = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="uploaded file was empty")
    if len(content) > _MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF too large ({len(content)} bytes; max {_MAX_PDF_BYTES})",
        )
    if not content.startswith(_PDF_MAGIC):
        raise HTTPException(
            status_code=400,
            detail="uploaded file does not start with %PDF — not a valid PDF",
        )

    path = store_pdf(doi_clean, content, cache_dir, source="dropzone")
    if path is None:
        raise HTTPException(status_code=500, detail="failed to write PDF to cache")

    logger.info("pdf_dropzone_stored", doi=doi_clean, size_bytes=len(content),
                  path=str(path))
    return JSONResponse({
        "doi": doi_clean,
        "stored": True,
        "size_bytes": len(content),
        "path": str(path),
    })


@router.get("/pdf-dropzone/{doi:path}")
async def pdf_dropzone_check(doi: str) -> dict[str, Any]:
    """Check whether a cached PDF is already available for a DOI."""
    cfg = getattr(app_state.config, "pdf_download", None) if app_state.config else None
    cache_dir = getattr(cfg, "cache_dir", None) if cfg else None
    if not cache_dir:
        raise HTTPException(status_code=400, detail="pdf_download.cache_dir not configured")
    doi_clean = doi.strip().replace("https://doi.org/", "").replace("http://doi.org/", "")
    path = cached_pdf_path(doi_clean, cache_dir)
    if path is None:
        return {"doi": doi_clean, "cached": False}
    return {
        "doi": doi_clean,
        "cached": True,
        "path": str(path),
        "size_bytes": path.stat().st_size,
    }
