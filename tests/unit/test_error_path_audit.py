"""End-to-end error-path audit (Wave 3.4)."""
from unittest.mock import MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.agent_cli import AgentCLIClient
from perspicacite.llm.client import AsyncLLMClient
from perspicacite.llm.errors import AuthError, RateLimitError


def _make_proc_mock(returncode: int, stderr: bytes, stdout: bytes = b""):
    class _FakeProc:
        def __init__(self):
            self.returncode = returncode

        async def communicate(self, _stdin=None):
            return stdout, stderr

        async def wait(self):
            return self.returncode

        def kill(self):
            pass

    async def factory(*args, **kwargs):
        return _FakeProc()

    return factory


@pytest.mark.asyncio
async def test_codex_auth_expired_raises_auth_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="agent_cli",
        output_format="text",
    )
    factory = _make_proc_mock(
        returncode=1,
        stderr=b"Please run 'codex login' to authenticate first.",
    )
    with patch("asyncio.create_subprocess_exec", new=factory), pytest.raises(AuthError) as exc:
        await cli.complete([{"role": "user", "content": "hi"}])
    assert exc.value.provider == "agent_cli"


@pytest.mark.asyncio
async def test_claude_binary_missing_raises_friendly_runtime_error():
    cli = AgentCLIClient(
        executable="/nonexistent-binary-perspicacite-test",
        provider_label="claude_cli",
        output_format="text",
    )
    async def factory(*args, **kwargs):
        raise FileNotFoundError("No such file")
    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RuntimeError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])
    msg = str(exc.value)
    assert "claude_cli" in msg
    assert "nonexistent" in msg or "Install" in msg or "executable" in msg


@pytest.mark.asyncio
async def test_litellm_auth_error_wrapped(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    class FakeAuth(Exception):
        pass
    FakeAuth.__name__ = "AuthenticationError"

    async def boom(*args, **kwargs):
        raise FakeAuth("AuthenticationError: invalid x-api-key")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(AuthError) as exc:
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )
    assert exc.value.provider == "anthropic"


@pytest.mark.asyncio
async def test_missing_api_key_message_raises_auth_error(tmp_path):
    cfg = LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,
        cache_path=tmp_path / "no.db",
    )
    client = AsyncLLMClient(cfg)

    async def boom(*args, **kwargs):
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")

    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock()
        litellm.acompletion = boom
        mock_get.return_value = litellm
        with pytest.raises(AuthError):
            await client.complete(
                messages=[{"role": "user", "content": "hi"}],
                cache=False,
            )


@pytest.mark.asyncio
async def test_rate_limit_wins_when_both_patterns_match():
    """Rate-limit detection runs before auth detection, so a message
    containing both signals stays a RateLimitError."""
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )
    factory = _make_proc_mock(
        returncode=1,
        stderr=b"Rate limit reached. Try again in 5m. (HTTP 401)",
    )
    with patch("asyncio.create_subprocess_exec", new=factory), pytest.raises(RateLimitError):
        await cli.complete([{"role": "user", "content": "hi"}])
