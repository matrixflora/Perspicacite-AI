"""detect_auth_error patterns + AuthError shape (Wave 3.4)."""
from perspicacite.llm.errors import (
    AuthError,
    LLMError,
    detect_auth_error,
)


def test_detects_authentication_failed():
    assert detect_auth_error("AuthenticationError: invalid API key")


def test_detects_api_key_missing():
    assert detect_auth_error("OPENAI_API_KEY environment variable not set")


def test_detects_401():
    assert detect_auth_error("HTTP 401 Unauthorized")


def test_detects_codex_login_prompt():
    assert detect_auth_error("Please run 'codex login' to authenticate")


def test_non_matching_returns_false():
    assert not detect_auth_error("Some other error")
    assert not detect_auth_error("")


def test_auth_error_provider_field():
    err = AuthError("anthropic: API key missing", provider="anthropic")
    assert isinstance(err, LLMError)
    assert err.provider == "anthropic"
