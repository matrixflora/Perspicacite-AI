"""CLI build-capsule + build-capsules surface."""

from __future__ import annotations

from click.testing import CliRunner

from perspicacite.cli import cli


def test_build_capsule_help():
    r = CliRunner().invoke(cli, ["build-capsule", "--help"])
    assert r.exit_code == 0, r.output
    assert "--paper" in r.output
    assert "--kb" in r.output


def test_build_capsules_help():
    r = CliRunner().invoke(cli, ["build-capsules", "--help"])
    assert r.exit_code == 0, r.output
    assert "--kb" in r.output
