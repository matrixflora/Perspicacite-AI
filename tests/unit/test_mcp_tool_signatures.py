"""Smoke tests that verify MCP tool function signatures contain expected parameters.

These tests catch signature regressions without requiring a running server or
external services. They are intentionally lightweight — the goal is to document
the public API contract and fail fast when a parameter is accidentally removed.
"""

from __future__ import annotations

import inspect


def test_generate_report_accepts_domains_parameter() -> None:
    """generate_report must accept a domains list parameter for adapter-aware extraction."""
    from perspicacite.mcp.server import generate_report

    sig = inspect.signature(generate_report)
    assert "domains" in sig.parameters, (
        "generate_report must accept a 'domains' parameter for adapter-aware claim extraction"
    )


def test_generate_report_domains_defaults_to_none() -> None:
    """generate_report domains parameter must default to None (backward-compat)."""
    from perspicacite.mcp.server import generate_report

    sig = inspect.signature(generate_report)
    param = sig.parameters["domains"]
    assert param.default is None, (
        f"generate_report 'domains' parameter must default to None, got {param.default!r}"
    )


def test_extract_claims_from_passages_accepts_domains_parameter() -> None:
    """extract_claims_from_passages must accept a domains list parameter."""
    from perspicacite.mcp.server import extract_claims_from_passages

    sig = inspect.signature(extract_claims_from_passages)
    assert "domains" in sig.parameters, (
        "extract_claims_from_passages must accept a 'domains' parameter"
    )
