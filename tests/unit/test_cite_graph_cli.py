"""Smoke test for the CLI subcommand `kb enrich-cite-graph`."""
from __future__ import annotations


def test_cli_subcommand_registered():
    """The CLI should expose `enrich-cite-graph` either as a `kb` subcommand
    or as a top-level command. Either is acceptable for v1."""
    from perspicacite.cli import cli
    # Walk the command tree to find it.
    found = False

    # Top-level command?
    cmds = getattr(cli, "commands", None)
    if cmds:
        if "enrich-cite-graph" in cmds:
            found = True
        # As a `kb` group subcommand?
        kb = cmds.get("kb")
        if kb and hasattr(kb, "commands") and "enrich-cite-graph" in kb.commands:
            found = True

    assert found, "expected `enrich-cite-graph` to be registered (top-level or under `kb`)"
