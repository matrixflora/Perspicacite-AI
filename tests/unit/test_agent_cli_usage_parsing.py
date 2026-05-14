"""Usage-path parsing in AgentCLIClient (Wave 2.3)."""
import json

import pytest

from perspicacite.llm.agent_cli import AgentCLIClient


def _client(**kw):
    """Build a minimal client just to exercise parsing methods."""
    defaults = dict(
        executable="/bin/true",
        output_format="json",
        result_json_path="result",
    )
    defaults.update(kw)
    return AgentCLIClient(**defaults)


def test_parses_usage_when_paths_set_and_json_valid():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hello",
        "usage": {"input_tokens": 42, "output_tokens": 7},
    })
    text, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert text == "hello"
    assert usage_in == 42
    assert usage_out == 7


def test_partial_hit_uses_zero_for_missing_path():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": 5},  # output_tokens missing
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert usage_in == 5
    assert usage_out == 0


def test_no_paths_returns_zero_zero():
    cli = _client()  # no usage paths
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": 100, "output_tokens": 50},
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert usage_in == 0
    assert usage_out == 0


def test_non_json_output_returns_zero_zero():
    cli = _client(
        output_format="text",
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    text, usage_in, usage_out = cli._parse_output_with_usage("plain text output")
    assert text == "plain text output"
    assert (usage_in, usage_out) == (0, 0)


def test_path_resolves_to_non_int_returns_zero():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    raw = json.dumps({
        "result": "hi",
        "usage": {"input_tokens": "not-a-number", "output_tokens": [1, 2]},
    })
    _, usage_in, usage_out = cli._parse_output_with_usage(raw)
    assert (usage_in, usage_out) == (0, 0)


def test_malformed_json_returns_zero_zero():
    cli = _client(
        usage_input_tokens_path="usage.input_tokens",
        usage_output_tokens_path="usage.output_tokens",
    )
    text, usage_in, usage_out = cli._parse_output_with_usage("not-json {{{")
    # text falls through to raw, usage is 0/0
    assert text == "not-json {{{"
    assert (usage_in, usage_out) == (0, 0)
