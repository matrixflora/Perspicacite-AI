"""Provenance recording for RAG answers.

Collector + contextvar wiring let modes / the LLM client append retrieval,
trace, and LLM-call events to a per-request record without changing call
signatures. See docs/superpowers/specs/2026-05-13-provenance-and-infra-expansion-design.md.
"""

from perspicacite.provenance.collector import (
    LLMCallRecord,
    ProvenanceCollector,
    RetrievalEvent,
)
from perspicacite.provenance.context import collecting, get_collector, set_collector

__all__ = [
    "LLMCallRecord",
    "ProvenanceCollector",
    "RetrievalEvent",
    "collecting",
    "get_collector",
    "set_collector",
]
