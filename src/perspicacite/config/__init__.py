"""Configuration system for Perspicacité v2."""

from perspicacite.config.loader import load_config
from perspicacite.config.schema import Config

__all__ = ["Config", "load_config"]
