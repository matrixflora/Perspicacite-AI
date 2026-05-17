"""Contextvar so the LLM client can find the active ProvenanceCollector."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from perspicacite.provenance.collector import ProvenanceCollector

current_collector: ContextVar[ProvenanceCollector | None] = ContextVar(
    "perspicacite_provenance_collector", default=None
)


def get_collector() -> ProvenanceCollector | None:
    return current_collector.get()


def set_collector(c: ProvenanceCollector | None) -> Any:
    return current_collector.set(c)


@contextmanager
def collecting(c: ProvenanceCollector) -> Iterator[ProvenanceCollector]:
    token = current_collector.set(c)
    try:
        yield c
    finally:
        current_collector.reset(token)
