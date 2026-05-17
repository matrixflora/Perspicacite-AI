"""End-to-end: BudgetTracker accumulates from real complete() calls (Wave 2.4)."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from perspicacite.config.schema import LLMConfig
from perspicacite.llm.budget import (
    BudgetExceededError,
    BudgetTracker,
    set_budget_tracker,
)
from perspicacite.llm.client import AsyncLLMClient


def _mk_config(tmp_path: Path) -> LLMConfig:
    return LLMConfig(
        default_provider="anthropic",
        default_model="claude-haiku-4-5",
        cache_enabled=False,  # avoid cache interaction
        cache_path=tmp_path / "no.db",
    )


def _resp(text: str, in_t: int, out_t: int):
    msg = MagicMock(); msg.content = text
    choice = MagicMock(); choice.message = msg
    r = MagicMock(); r.choices = [choice]
    r.get = MagicMock(side_effect=lambda k, d=None: {
        "usage": {"prompt_tokens": in_t, "completion_tokens": out_t}
    }.get(k, d))
    return r


@pytest.mark.asyncio
async def test_tracker_accumulates_from_complete_calls(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    tracker = BudgetTracker()
    token = set_budget_tracker(tracker)
    try:
        fake = AsyncMock(return_value=_resp("hi", 100, 50))
        with patch.object(client, "_get_litellm") as mock_get:
            litellm = MagicMock(); litellm.acompletion = fake
            mock_get.return_value = litellm
            await client.complete(messages=[{"role": "user", "content": "hi"}])
        s = tracker.summary()
        assert s["tokens_in"] == 100
        assert s["tokens_out"] == 50
        assert s["usd"] > 0  # haiku is priced
    finally:
        import perspicacite.llm.budget as _b
        _b._tracker.reset(token)


@pytest.mark.asyncio
async def test_tracker_breach_raises_mid_pipeline(tmp_path):
    client = AsyncLLMClient(_mk_config(tmp_path))
    tracker = BudgetTracker(max_input_tokens=150, action="abort")
    token = set_budget_tracker(tracker)
    try:
        fake = AsyncMock(return_value=_resp("ok", 100, 10))
        with patch.object(client, "_get_litellm") as mock_get:
            litellm = MagicMock(); litellm.acompletion = fake
            mock_get.return_value = litellm
            # First call: 100 in, under cap.
            await client.complete(messages=[{"role": "user", "content": "a"}])
            # Second call would push to 200 in, over cap.
            with pytest.raises(BudgetExceededError):
                await client.complete(messages=[{"role": "user", "content": "b"}])
    finally:
        import perspicacite.llm.budget as _b
        _b._tracker.reset(token)


@pytest.mark.asyncio
async def test_no_tracker_no_change(tmp_path):
    """Without a tracker installed, behaviour matches today."""
    client = AsyncLLMClient(_mk_config(tmp_path))
    fake = AsyncMock(return_value=_resp("ok", 1_000_000, 1_000_000))
    with patch.object(client, "_get_litellm") as mock_get:
        litellm = MagicMock(); litellm.acompletion = fake
        mock_get.return_value = litellm
        # Should NOT raise even though token counts are huge.
        result = await client.complete(messages=[{"role": "user", "content": "a"}])
        assert result == "ok"
