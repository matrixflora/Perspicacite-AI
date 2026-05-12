"""Live test for the Anthropic API via AsyncLLMClient.

Requires ANTHROPIC_API_KEY to be set. Excluded from the default unit test run.

Run with:
    uv run pytest tests/test_anthropic_api.py -v
"""

import os

import pytest

from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient

pytestmark = pytest.mark.live


@pytest.fixture
def anthropic_client():
    config = LLMConfig(
        default_provider="anthropic",
        providers={
            "anthropic": LLMProviderConfig(
                base_url="https://api.anthropic.com",
                timeout=60,
                max_retries=1,
            )
        },
    )
    return AsyncLLMClient(config)


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
@pytest.mark.asyncio
async def test_anthropic_complete(anthropic_client):
    """Verify a basic completion round-trip with the Anthropic API."""
    response = await anthropic_client.complete(
        messages=[{"role": "user", "content": "Reply with exactly the word: PONG"}],
        model="claude-3-5-haiku-20241022",
        provider="anthropic",
    )
    assert isinstance(response, str)
    assert len(response) > 0
    assert "PONG" in response.upper()


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set",
)
@pytest.mark.asyncio
async def test_anthropic_stream(anthropic_client):
    """Verify streaming works end-to-end with the Anthropic API."""
    chunks = []
    async for chunk in anthropic_client.stream(
        messages=[{"role": "user", "content": "Count from 1 to 3, one number per line."}],
        model="claude-3-5-haiku-20241022",
        provider="anthropic",
    ):
        chunks.append(chunk)

    full = "".join(chunks)
    assert "1" in full
    assert "2" in full
    assert "3" in full
