"""Pattern detection + structured-exception tests for Wave 3.1."""

from perspicacite.llm.errors import (
    LLMError,
    RateLimitError,
    detect_rate_limit,
    suggested_action,
)


def test_claude_code_rate_limit_with_minutes():
    text = "Rate limit reached. Try again in 1h 23m."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds == 1 * 3600 + 23 * 60


def test_claude_code_rate_limit_minutes_only():
    text = "Rate limit reached. Try again in 47m."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds == 47 * 60


def test_claude_code_usage_limit_no_minutes():
    text = "Usage limit exceeded. Resets at 5pm."
    hit = detect_rate_limit(text)
    assert hit is not None
    assert hit.retry_after_seconds is None


def test_codex_429():
    text = "Error: HTTP 429 Too Many Requests"
    assert detect_rate_limit(text) is not None


def test_generic_too_many_requests():
    text = "API responded: Too Many Requests"
    assert detect_rate_limit(text) is not None


def test_non_matching_returns_none():
    assert detect_rate_limit("Some unrelated error") is None
    assert detect_rate_limit("") is None


def test_suggested_action_anthropic_mentions_fallback():
    msg = suggested_action("anthropic")
    assert "fallback" in msg.lower() or "providers_per_stage" in msg or "fallback" in msg.lower() \
        or "route" in msg.lower()


def test_suggested_action_claude_cli_mentions_direct_api():
    msg = suggested_action("claude_cli")
    assert "anthropic" in msg.lower() or "direct" in msg.lower()


def test_suggested_action_default_for_unknown_provider():
    msg = suggested_action("totally-made-up-provider")
    assert isinstance(msg, str)
    assert len(msg) > 0


def test_rate_limit_error_is_llm_error():
    err = RateLimitError("test", provider="anthropic")
    assert isinstance(err, LLMError)
    assert err.provider == "anthropic"
    assert err.retry_after_seconds is None
