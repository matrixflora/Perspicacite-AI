from perspicacite.config.schema import Config, MultimodalConfig


def test_defaults():
    c = MultimodalConfig()
    assert c.enabled is True
    assert c.max_images == 6
    assert any(p.startswith("anthropic/claude-") for p in c.vision_allowlist)
    assert any(p.startswith("gpt-4o") for p in c.vision_allowlist)


def test_config_has_multimodal():
    cfg = Config()
    assert isinstance(cfg.multimodal, MultimodalConfig)
    assert cfg.multimodal.max_images == 6
