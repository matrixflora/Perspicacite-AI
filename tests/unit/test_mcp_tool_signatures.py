"""Smoke tests that verify MCP tool function signatures contain expected parameters.

These tests catch signature regressions without requiring a running server or
external services. They are intentionally lightweight — the goal is to document
the public API contract and fail fast when a parameter is accidentally removed.
"""

from __future__ import annotations

import inspect


def test_generate_report_accepts_domain_parameter() -> None:
    """generate_report must accept a domain parameter for adapter-aware extraction."""
    from perspicacite.mcp.server import generate_report

    sig = inspect.signature(generate_report)
    assert "domain" in sig.parameters, (
        "generate_report must accept a 'domain' parameter for adapter-aware claim extraction"
    )


def test_generate_report_domain_defaults_to_none() -> None:
    """generate_report domain parameter must default to None (backward-compat)."""
    from perspicacite.mcp.server import generate_report

    sig = inspect.signature(generate_report)
    param = sig.parameters["domain"]
    assert param.default is None, (
        f"generate_report 'domain' parameter must default to None, got {param.default!r}"
    )


def test_extract_claims_from_passages_accepts_domain_parameter() -> None:
    """extract_claims_from_passages must also accept a domain parameter (baseline)."""
    from perspicacite.mcp.server import extract_claims_from_passages

    sig = inspect.signature(extract_claims_from_passages)
    assert "domain" in sig.parameters, (
        "extract_claims_from_passages must accept a 'domain' parameter"
    )
