
import httpx
import pytest
import respx

from perspicacite.pipeline.external.http import (
    http_get_bytes,
    http_get_json,
    http_get_text,
)


@pytest.mark.asyncio
async def test_http_get_json_caches(tmp_path):
    url = "https://api.example.com/x"
    payload = {"hello": "world"}
    with respx.mock(assert_all_called=False) as m:
        route = m.get(url).respond(200, json=payload)
        a = await http_get_json(url, cache_dir=tmp_path, api="x", query="q")
        b = await http_get_json(url, cache_dir=tmp_path, api="x", query="q")
        assert a == payload
        assert b == payload
        assert route.call_count == 1  # second call served from cache


@pytest.mark.asyncio
async def test_http_get_json_retries_on_5xx(tmp_path):
    url = "https://api.example.com/retry"
    with respx.mock(assert_all_called=False) as m:
        route = m.get(url)
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(503),
            httpx.Response(200, json={"ok": 1}),
        ]
        result = await http_get_json(
            url, cache_dir=tmp_path, api="x", query="q", max_retries=3,
        )
        assert result == {"ok": 1}
        assert route.call_count == 3


@pytest.mark.asyncio
async def test_http_get_json_returns_none_on_4xx(tmp_path):
    url = "https://api.example.com/missing"
    with respx.mock(assert_all_called=False) as m:
        m.get(url).respond(404)
        result = await http_get_json(
            url, cache_dir=tmp_path, api="x", query="q", max_retries=1,
        )
        assert result is None


@pytest.mark.asyncio
async def test_http_get_text_max_bytes_cap(tmp_path):
    url = "https://api.example.com/big"
    with respx.mock(assert_all_called=False) as m:
        m.get(url).respond(200, text="x" * 10_000)
        result = await http_get_text(
            url, cache_dir=tmp_path, api="x", query="q", max_bytes=1000,
        )
        assert result is None


@pytest.mark.asyncio
async def test_http_get_bytes_roundtrip(tmp_path):
    url = "https://api.example.com/blob"
    data = b"\x89PNG\r\n\x1a\nfake"
    with respx.mock(assert_all_called=False) as m:
        route = m.get(url).respond(200, content=data)
        a = await http_get_bytes(url, cache_dir=tmp_path, api="x", query="q")
        b = await http_get_bytes(url, cache_dir=tmp_path, api="x", query="q")
        assert a == data
        assert b == data
        assert route.call_count == 1  # cached on second call


@pytest.mark.asyncio
async def test_http_get_bytes_max_bytes_cap(tmp_path):
    url = "https://api.example.com/big-blob"
    with respx.mock(assert_all_called=False) as m:
        m.get(url).respond(200, content=b"x" * 10_000)
        result = await http_get_bytes(
            url, cache_dir=tmp_path, api="x", query="q", max_bytes=1000,
        )
        assert result is None


@pytest.mark.asyncio
async def test_http_get_json_handles_timeout(tmp_path):
    url = "https://api.example.com/timeout"
    with respx.mock(assert_all_called=False) as m:
        m.get(url).side_effect = httpx.TimeoutException("timed out")
        result = await http_get_json(
            url, cache_dir=tmp_path, api="x", query="q", max_retries=1,
        )
        assert result is None
