"""Async httpx wrappers with cache + retry + size caps."""
from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import httpx

from perspicacite.logging import get_logger
from perspicacite.pipeline.external.cache import cache_load, cache_path, cache_store

logger = get_logger("perspicacite.external.http")


async def _request_with_retries(
    url: str, *,
    headers: dict[str, str] | None,
    timeout: float,
    max_retries: int,
) -> httpx.Response | None:
    backoff = 1.0
    last_status: int | None = None
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(1, max_retries + 1):
            try:
                resp = await client.get(url, headers=headers or {})
                last_status = resp.status_code
                if resp.status_code < 400:
                    return resp
                if resp.status_code < 500 and resp.status_code != 429:
                    # Client error, no retry
                    logger.warning(
                        "external_http_client_error",
                        url=url, status=resp.status_code,
                    )
                    return None
                logger.warning(
                    "external_http_retryable",
                    url=url, status=resp.status_code, attempt=attempt,
                )
            except (httpx.HTTPError, OSError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "external_http_error",
                    url=url, error=str(exc), attempt=attempt,
                )
            if attempt < max_retries:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
    logger.warning("external_http_exhausted", url=url, last_status=last_status)
    return None


async def http_get_json(
    url: str, *, cache_dir: Path, api: str, query: str,
    ttl_seconds: int = 30 * 86400,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
) -> Any | None:
    path = cache_path(cache_dir, api, query)
    cached = cache_load(path, ttl_seconds=ttl_seconds)
    if cached is not None:
        return cached
    resp = await _request_with_retries(
        url, headers=headers, timeout=timeout, max_retries=max_retries,
    )
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError:
        logger.warning("external_http_json_decode_failed", url=url)
        return None
    cache_store(path, data)
    return data


async def http_get_text(
    url: str, *, cache_dir: Path, api: str, query: str,
    ttl_seconds: int = 30 * 86400,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    max_retries: int = 3,
    max_bytes: int | None = None,
) -> str | None:
    path = cache_path(cache_dir, api, query)
    cached = cache_load(path, ttl_seconds=ttl_seconds)
    if isinstance(cached, str):
        return cached
    resp = await _request_with_retries(
        url, headers=headers, timeout=timeout, max_retries=max_retries,
    )
    if resp is None:
        return None
    text = resp.text
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        logger.warning("external_http_text_exceeds_cap", url=url, max_bytes=max_bytes)
        return None
    cache_store(path, text)
    return text


async def http_get_bytes(
    url: str, *, cache_dir: Path, api: str, query: str,
    ttl_seconds: int = 30 * 86400,
    headers: dict[str, str] | None = None,
    timeout: float = 60.0,
    max_retries: int = 3,
    max_bytes: int | None = None,
) -> bytes | None:
    path = cache_path(cache_dir, api, query)
    cached = cache_load(path, ttl_seconds=ttl_seconds)
    if isinstance(cached, str):
        try:
            return base64.b64decode(cached)
        except (ValueError, TypeError):
            pass
    resp = await _request_with_retries(
        url, headers=headers, timeout=timeout, max_retries=max_retries,
    )
    if resp is None:
        return None
    data = resp.content
    if max_bytes is not None and len(data) > max_bytes:
        logger.warning("external_http_bytes_exceeds_cap", url=url, max_bytes=max_bytes)
        return None
    cache_store(path, base64.b64encode(data).decode("ascii"))
    return data
