"""Tests for GoogleScholarConfig OpenRouter fallback fields."""


def test_google_scholar_config_openrouter_defaults():
    from perspicacite.config.schema import GoogleScholarConfig

    cfg = GoogleScholarConfig()
    assert cfg.openrouter_fallback_enabled is False
    assert cfg.openrouter_api_key == ""
    assert cfg.openrouter_fallback_model == "deepseek/deepseek-chat"
    assert "arxiv.org" in cfg.openrouter_fallback_domains
    assert "pubmed.ncbi.nlm.nih.gov" in cfg.openrouter_fallback_domains
    assert len(cfg.openrouter_fallback_domains) >= 8


def test_google_scholar_config_openrouter_fields_settable():
    from perspicacite.config.schema import GoogleScholarConfig

    cfg = GoogleScholarConfig(
        openrouter_fallback_enabled=False,
        openrouter_api_key="sk-test",
        openrouter_fallback_model="openai/gpt-4o-mini",
        openrouter_fallback_domains=["arxiv.org"],
    )
    assert cfg.openrouter_fallback_enabled is False
    assert cfg.openrouter_api_key == "sk-test"
    assert cfg.openrouter_fallback_model == "openai/gpt-4o-mini"
    assert cfg.openrouter_fallback_domains == ["arxiv.org"]


def test_google_scholar_config_domains_are_independent_instances():
    """Two GoogleScholarConfig instances have independent domain lists (default_factory)."""
    from perspicacite.config.schema import GoogleScholarConfig
    cfg1 = GoogleScholarConfig()
    cfg2 = GoogleScholarConfig()
    assert cfg1.openrouter_fallback_domains is not cfg2.openrouter_fallback_domains


def test_openrouter_web_in_paper_source_members():
    from perspicacite.models.papers import PaperSource
    assert PaperSource.OPENROUTER_WEB in list(PaperSource)
