# src/perspicacite/integrations/zotero_resources.py
"""ResourceLocator: ordered file access lists for Zotero items.

Returns local paths (verified on disk) before remote URLs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

# Type alias for resource dicts (not exported as a class, kept as dict for JSON-compatibility)
Resource = dict[str, Any]


def _sanitize_capsule_id(doi: str) -> str:
    """Mirror the capsule_builder sanitisation: ':' → '_', '/' → '__'."""
    clean = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
    return clean.replace(":", "_").replace("/", "__")


class ResourceLocator:
    """Build ordered access lists for a paper's files.

    Call ``build(doi, zotero_item, attachments)`` to get a list of resource
    dicts matching the zotero_get_paper_resources response schema.
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def build(
        self,
        *,
        doi: str,
        zotero_item: dict[str, Any],
        attachments: list[dict[str, Any]],
    ) -> list[Resource]:
        resources: list[Resource] = []

        # --- Full-text PDF ---
        pdf_access = self._pdf_access(doi, attachments)
        resources.append({
            "role": "fulltext_pdf",
            "filename": "paper.pdf",
            "access": pdf_access,
        })

        # --- Supplementary files ---
        for si in self._si_files(doi):
            resources.append(si)

        return resources

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _pdf_access(
        self, doi: str, attachments: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        access: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        # 1. Local PDF cache (verified on disk)
        local = self._local_pdf_path(doi)
        if local is not None:
            access.append({"type": "local", "path": str(local)})

        # 2. Publisher URL from Zotero attachments (linked_url or imported PDF)
        for att in attachments:
            data = att.get("data") or {}
            ct = data.get("contentType") or ""
            url = data.get("url") or ""
            if "pdf" in ct.lower() and url and url not in seen_urls:
                access.append({"type": "remote", "url": url, "via": "publisher"})
                seen_urls.add(url)

        # 3. DOI resolver (always last, de-duplicated)
        doi_url = f"https://doi.org/{doi}"
        if doi_url not in seen_urls:
            access.append({"type": "remote", "url": doi_url, "via": "doi_resolver"})

        return access

    def _local_pdf_path(self, doi: str) -> Path | None:
        pdf_cfg = getattr(self._config, "pdf_download", None)
        if pdf_cfg is None or not getattr(pdf_cfg, "cache_pdfs", False):
            return None
        cache_dir = getattr(pdf_cfg, "cache_dir", None)
        if not cache_dir:
            return None
        from perspicacite.pipeline.download.pdf_cache import cached_pdf_path
        return cached_pdf_path(doi, cache_dir)

    def _si_files(self, doi: str) -> list[Resource]:
        cap_cfg = getattr(self._config, "capsule", None)
        if cap_cfg is None:
            return []
        root = getattr(cap_cfg, "root", None)
        if not root:
            return []
        paper_id = _sanitize_capsule_id(doi)
        si_dir = Path(root) / paper_id / "supplementary" / "files"
        if not si_dir.is_dir():
            return []
        result = []
        for f in sorted(si_dir.iterdir()):
            if f.is_file():
                result.append({
                    "role": "supplementary",
                    "filename": f.name,
                    "access": [{"type": "local", "path": str(f)}],
                })
        return result
