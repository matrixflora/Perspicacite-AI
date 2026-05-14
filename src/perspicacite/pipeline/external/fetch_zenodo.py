"""Synced from AgenticScienceBuilder @ a10eced — httpx-adapted with Perspicacité
size caps on small-file fetch (V2 extension).

Zenodo records expose ``record["files"]`` with ``links.self`` blob URLs.
By default fetch metadata only. Opt-in small-file fetch is hard-capped to
prevent dataset blob downloads.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from perspicacite.logging import get_logger
from perspicacite.pipeline.external.http import http_get_bytes, http_get_json

logger = get_logger("perspicacite.external.zenodo")

ZENODO_ARCHIVE_EXTS = (".zip", ".tar", ".tar.gz", ".tgz", ".7z", ".rar")


def _ext_of(filename: str) -> str:
    name = filename.lower()
    for compound in (".tar.gz",):
        if name.endswith(compound):
            return compound
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[1]


def _is_archive(filename: str) -> bool:
    name = filename.lower()
    return any(name.endswith(ext) for ext in ZENODO_ARCHIVE_EXTS)


async def fetch_zenodo(
    record_id: str, *,
    capsule_dir: Path,
    cache_dir: Path,
    text_file_extensions: list[str] | tuple[str, ...] = (),
    max_bytes_per_file: int = 500_000,
    max_bytes_per_record: int = 5_000_000,
    metadata_only: bool = True,
    ttl_seconds: int = 30 * 86400,
) -> dict[str, Any]:
    """Fetch a Zenodo record's metadata (and optionally small text/code files).

    Returns ``{"record_id": str, "metadata_path": str | None,
    "files_fetched": int, "bytes_fetched": int, "paths": [...]}``.
    """
    base = capsule_dir / "external" / "zenodo"
    base.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "record_id": record_id,
        "metadata_path": None,
        "files_fetched": 0,
        "bytes_fetched": 0,
        "paths": [],
    }

    url = f"https://zenodo.org/api/records/{record_id}"
    meta = await http_get_json(
        url, cache_dir=cache_dir, api="zenodo", query=record_id,
        ttl_seconds=ttl_seconds,
    )
    if meta is None:
        return summary

    meta_path = base / f"{record_id}.json"
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    summary["metadata_path"] = str(meta_path)
    summary["paths"].append(f"zenodo/{record_id}.json")

    if metadata_only:
        return summary

    allowed = {ext.lower() for ext in text_file_extensions}
    if not allowed:
        return summary

    files_dir = base / record_id / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    running_total = 0

    for entry in (meta.get("files") or []):
        key = entry.get("key") or entry.get("filename") or ""
        if not key:
            continue
        if _is_archive(key):
            logger.info("zenodo_skip_archive", record=record_id, file=key)
            continue
        ext = _ext_of(key)
        if ext.lower() not in allowed:
            continue
        declared_size = int(entry.get("size") or 0)
        if declared_size and declared_size > max_bytes_per_file:
            logger.info(
                "zenodo_skip_oversize_file", record=record_id, file=key,
                size=declared_size, cap=max_bytes_per_file,
            )
            continue
        if running_total + (declared_size or 0) > max_bytes_per_record:
            logger.info(
                "zenodo_record_budget_exceeded", record=record_id,
                running_total=running_total, cap=max_bytes_per_record,
            )
            break
        blob_url = ((entry.get("links") or {}).get("self")) or entry.get("link")
        if not blob_url:
            continue
        data = await http_get_bytes(
            blob_url, cache_dir=cache_dir, api="zenodo_blob",
            query=f"{record_id}:{key}",
            ttl_seconds=ttl_seconds, max_bytes=max_bytes_per_file,
        )
        if data is None:
            continue
        if running_total + len(data) > max_bytes_per_record:
            logger.info(
                "zenodo_record_budget_exceeded_post_fetch",
                record=record_id, would_be=running_total + len(data),
            )
            break
        out_path = files_dir / key.replace("/", "__")
        out_path.write_bytes(data)
        running_total += len(data)
        summary["files_fetched"] += 1
        summary["bytes_fetched"] += len(data)
        summary["paths"].append(f"zenodo/{record_id}/files/{out_path.name}")

    return summary
