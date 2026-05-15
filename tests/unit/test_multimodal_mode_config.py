import pytest
from pydantic import ValidationError

from perspicacite.config.schema import MultimodalConfig, MultimodalMode


def test_mode_enum_values():
    assert MultimodalMode.OFF.value == "off"
    assert MultimodalMode.AUTO.value == "auto"
    assert MultimodalMode.FORCE.value == "force"


def test_default_mode_is_auto():
    cfg = MultimodalConfig()
    assert cfg.mode == MultimodalMode.AUTO


def test_default_show_code_is_false():
    cfg = MultimodalConfig()
    assert cfg.show_code is False


def test_mode_accepts_string_values():
    cfg = MultimodalConfig(mode="force")
    assert cfg.mode == MultimodalMode.FORCE


def test_invalid_mode_rejected():
    with pytest.raises(ValidationError):
        MultimodalConfig(mode="loud")


def test_show_code_true_round_trip():
    cfg = MultimodalConfig(show_code=True)
    assert cfg.show_code is True
