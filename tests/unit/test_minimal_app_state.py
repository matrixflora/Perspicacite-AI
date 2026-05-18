"""Unit tests for MinimalAppState."""
from unittest.mock import MagicMock, patch

from perspicacite.web.state_minimal import MinimalAppState


def test_minimal_app_state_direct_construction():
    cfg = MagicMock()
    state = MinimalAppState(config=cfg, llm_client="fake")
    assert state.config is cfg
    assert state.llm_client == "fake"


def test_minimal_app_state_from_config():
    """from_config constructs an LLM client without raising."""
    cfg = MagicMock()
    cfg.llm = MagicMock()
    cfg.llm.providers = []
    with patch("perspicacite.llm.client.AsyncLLMClient") as MockClient:
        MockClient.return_value = MagicMock()
        state = MinimalAppState.from_config(cfg)
        assert state.config is cfg
        assert state.llm_client is not None
        MockClient.assert_called_once_with(cfg)
