from perspicacite.config.schema import LLMConfig


def test_default_is_empty_dict():
    cfg = LLMConfig()
    assert cfg.embedding_models_per_type == {}


def test_accepts_per_type_map():
    cfg = LLMConfig(embedding_models_per_type={
        "code": "mistral/codestral-embed",
        "text": "text-embedding-3-small",
    })
    assert cfg.embedding_models_per_type["code"] == "mistral/codestral-embed"
    assert cfg.embedding_models_per_type["text"] == "text-embedding-3-small"
