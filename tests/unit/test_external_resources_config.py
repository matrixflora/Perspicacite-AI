from perspicacite.config.schema import Config, ExternalResourcesConfig


def test_defaults():
    c = ExternalResourcesConfig()
    assert c.mine is True
    assert c.fetch_on_demand is True
    assert c.cache_ttl_days == 30
    assert c.zenodo_max_bytes_per_file == 500_000
    assert c.zenodo_max_bytes_per_record == 5_000_000
    assert ".py" in c.text_file_extensions
    assert ".R" in c.text_file_extensions
    assert ".jl" in c.text_file_extensions
    assert ".ipynb" in c.text_file_extensions


def test_config_has_external_resources():
    cfg = Config()
    assert isinstance(cfg.external_resources, ExternalResourcesConfig)
    assert cfg.external_resources.cache_ttl_days == 30
