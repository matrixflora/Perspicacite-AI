"""Verify AgentCLIClient surfaces RateLimitError on rate-limit stderr (Wave 3.1)."""
from unittest.mock import AsyncMock, patch

import pytest

from perspicacite.llm.agent_cli import AgentCLIClient
from perspicacite.llm.errors import RateLimitError


def _make_proc_mock(returncode: int, stdout: bytes, stderr: bytes):
    """Return a coroutine that mimics asyncio.create_subprocess_exec
    enough for AgentCLIClient.complete."""

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
async def test_rate_limit_stderr_raises_rate_limit_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )

    factory = _make_proc_mock(
        returncode=1,
        stdout=b"",
        stderr=b"Rate limit reached. Try again in 47m.",
    )

    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RateLimitError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])

    assert exc.value.provider == "claude_cli"
    assert exc.value.retry_after_seconds == 47 * 60


@pytest.mark.asyncio
async def test_non_rate_limit_failure_raises_runtime_error():
    cli = AgentCLIClient(
        executable="/bin/echo",
        provider_label="claude_cli",
        output_format="text",
    )

    factory = _make_proc_mock(
        returncode=1,
        stdout=b"",
        stderr=b"Some other unrelated error",
    )

    with patch("asyncio.create_subprocess_exec", new=factory):
        with pytest.raises(RuntimeError) as exc:
            await cli.complete([{"role": "user", "content": "hi"}])

    # Should NOT be a RateLimitError.
    assert not isinstance(exc.value, RateLimitError)
