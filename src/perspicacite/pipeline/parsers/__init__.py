"""Document parsers for various formats."""

from perspicacite.pipeline.parsers.html import HTMLParser
from perspicacite.pipeline.parsers.pdf import PDFParser

__all__ = ["HTMLParser", "PDFParser"]
