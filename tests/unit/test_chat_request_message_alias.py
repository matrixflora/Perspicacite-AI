"""POST /api/chat must accept either {"query": ...} (canonical) or
{"message": ...} (Scriptorium-v0.13-compatible alias). Both should
populate ChatRequest.query."""


def test_chat_request_accepts_query():
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(query="hello")
    assert req.query == "hello"


def test_chat_request_accepts_message_alias():
    """Backward-compat for Scriptorium-v0.13 and clients reading
    the legacy OpenAPI schema field name."""
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(message="hello")  # type: ignore[call-arg]
    assert req.query == "hello"


def test_chat_request_prefers_query_when_both_supplied():
    from perspicacite.web.routers.chat import ChatRequest
    req = ChatRequest(query="canonical", message="alias")  # type: ignore[call-arg]
    assert req.query == "canonical"
