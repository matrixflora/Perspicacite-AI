"""CapsuleConfig defaults and nesting under root Config."""

from __future__ import annotations

from pathlib import Path

from perspicacite.config.schema import Config


def test_defaults():
    cfg = Config()
    assert cfg.capsule.enabled is True
    assert cfg.capsule.auto_build_on_ingest is True
    assert cfg.capsule.min_version == "0.1"
    assert isinstance(cfg.capsule.root, Path)
    assert cfg.capsule.root.name == "capsules"


def test_override_via_dict():
    cfg = Config(capsule={"enabled": False, "root": "/tmp/caps", "auto_build_on_ingest": False})
    assert cfg.capsule.enabled is False
    assert cfg.capsule.auto_build_on_ingest is False
    assert str(cfg.capsule.root) == "/tmp/caps"
