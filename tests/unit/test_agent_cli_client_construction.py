"""Verify config → AgentCLIClient wiring for usage paths (Wave 2.3)."""
from perspicacite.config.schema import LLMConfig, LLMProviderConfig
from perspicacite.llm.client import AsyncLLMClient


def test_usage_paths_flow_from_config_to_client():
    cfg = LLMConfig(
        default_provider="agent_cli",
        default_model="haiku",
        providers={
            "agent_cli": LLMProviderConfig(
                executable="/bin/echo",
                output_format="json",
                result_json_path="result",
                usage_input_tokens_path="usage.input_tokens",
                usage_output_tokens_path="usage.output_tokens",
            ),
        },
    )
    client = AsyncLLMClient(cfg)
    cli = client._get_agent_cli_client("agent_cli")
    assert cli.usage_input_tokens_path == "usage.input_tokens"
    assert cli.usage_output_tokens_path == "usage.output_tokens"
